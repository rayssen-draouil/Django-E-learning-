"""Class-based CRUD views for the learning content (courses, modules, mentors, quiz)."""

import csv

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView
from django.views.generic.detail import SingleObjectMixin

from .forms import (
    CourseForm,
    MentorForm,
    ModuleForm,
    QuestionForm,
    QuestionFormSetMixin,
    QuizForm,
)
from .mixins import EditorRequiredMixin, ErrorRedirectMixin, OwnerOrStaffMixin
from .models import Answer, Category, Course, CourseLevel, CourseStatus, Mentor, Module, Question, Quiz
from .permissions import can_edit, is_editor, visible_courses


class ActionMessageMixin:
    """Show a success message after a successful create or update."""

    success_message = ""

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(response, "url", None):
            messages.success(self.request, self.success_message.format(obj=self.object))
        return response


class DeleteMessageMixin:
    """Show a success message after a successful deletion."""

    delete_message = ""

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        response = super().delete(request, *args, **kwargs)
        messages.success(request, self.delete_message.format(obj=obj))
        return response


class EditorContextMixin:
    """Expose whether the current user may manage content to the templates."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_editor"] = is_editor(self.request.user)
        return context


class FormUserMixin:
    """Pass the current user to the form so it can restrict its querysets."""

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class PageTitleMixin:
    """Add a French page title and a cancel link to create and update views."""

    page_title = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = self.page_title
        if "back_url" not in context and hasattr(self, "get_error_url"):
            context["back_url"] = self.get_error_url()
        return context


class CourseOwnerOrStaffMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Allow access only to staff members and the course owner."""

    raise_exception = True

    def test_func(self):
        return can_edit(self.request.user, self.get_object())


class CourseCreateAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Allow authenticated users to create a course."""

    raise_exception = False

    def test_func(self):
        return self.request.user.is_authenticated


class CourseModuleCreateAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Allow staff and the owner of the target course to create modules."""

    raise_exception = True

    def test_func(self):
        course_id = self.request.POST.get("course") or self.request.GET.get("course")
        if not course_id:
            return False
        course = get_object_or_404(
            Course.all_objects.select_related("owner", "mentor__user"),
            pk=course_id,
        )
        return can_edit(self.request.user, course)


class FilterSortListMixin(ListView):
    """Keyword search, sorting and pagination helpers for the list pages."""

    paginate_by = 9
    search_fields: tuple = ()
    sort_options: dict = {}
    default_sort = ""

    def get_filtered_queryset(self):
        """View-specific filtering and annotations; overridden by each list view."""
        return super().get_queryset()

    def get_search_term(self) -> str:
        return self.request.GET.get("q", "").strip()

    def get_queryset(self):
        queryset = self.get_filtered_queryset()
        term = self.get_search_term()
        if term and self.search_fields:
            condition = Q()
            for field in self.search_fields:
                condition |= Q(**{f"{field}__icontains": term})
            queryset = queryset.filter(condition)
        sort = self.request.GET.get("sort", "") or self.default_sort
        ordering = self.sort_options.get(sort)
        if ordering:
            queryset = queryset.order_by(*ordering)
        return queryset

    def build_query_string(self, **overrides) -> str:
        """Current query string without the page parameter, with overrides applied."""
        params = self.request.GET.copy()
        params.pop("page", None)
        for key, value in overrides.items():
            if value:
                params[key] = value
            else:
                params.pop(key, None)
        return params.urlencode()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_sort = self.request.GET.get("sort", "") or self.default_sort
        context.update(
            search_term=self.get_search_term(),
            active_sort=active_sort,
            sort_links=[
                (key, "?" + self.build_query_string(sort=key))
                for key in self.sort_options
            ],
            query_string=self.build_query_string(),
        )
        return context


class CourseListView(EditorContextMixin, FilterSortListMixin):
    model = Course
    template_name = "users/learning/course_list.html"
    context_object_name = "courses"
    search_fields = ("title", "description", "short_description")
    sort_options = {
        "newest": ("-created_at",),
        "oldest": ("created_at",),
        "title": ("title",),
    }
    default_sort = "newest"

    def get_filtered_queryset(self):
        queryset = (
            visible_courses(self.request.user)
            .select_related("owner", "category", "mentor__user")
            .annotate(annotated_module_count=Count("modules", distinct=True))
        )
        level = self.request.GET.get("level", "")
        if level in CourseLevel.values:
            queryset = queryset.filter(level=level)
        category = self.request.GET.get("category", "")
        if category:
            queryset = queryset.filter(category_id=category)
        status = self.request.GET.get("status", "")
        if self.request.user.is_staff and status in CourseStatus.values:
            queryset = queryset.filter(status=status)
        return queryset

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        if request.GET.get("export") == "csv":
            if not request.user.is_staff:
                raise PermissionDenied
            return self.render_csv(self.object_list)
        context = self.get_context_data()
        return self.render_to_response(context)

    def render_csv(self, queryset):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="cours.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Titre",
            "Slug",
            "Catégorie",
            "Niveau",
            "Statut",
            "Propriétaire",
            "Durée (heures)",
            "Modules",
        ])
        for course in queryset:
            writer.writerow([
                course.title,
                course.slug,
                course.category.name if course.category else "",
                course.get_level_display(),
                course.get_status_display(),
                course.owner.get_full_name() if course.owner else "",
                course.duration_hours,
                course.modules_count,
            ])
        return response

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        action = request.POST.get("bulk_action")
        selected_ids = request.POST.getlist("selected_courses")
        if not action or not selected_ids:
            messages.error(request, "Sélectionnez au moins un cours et une action.")
            return redirect(f"{reverse('users:courses')}?{self.build_query_string()}")

        queryset = Course.all_objects.filter(pk__in=selected_ids)
        count = queryset.count()
        if action == "publish":
            queryset.update(status=CourseStatus.PUBLISHED, is_deleted=False, is_published=True)
            message = f"{count} cours publié(s)."
        elif action == "archive":
            queryset.update(status=CourseStatus.ARCHIVED, is_published=False)
            message = f"{count} cours archivé(s)."
        elif action == "delete":
            queryset.update(status=CourseStatus.ARCHIVED, is_deleted=True, is_published=False)
            message = f"{count} cours supprimé(s) logiquement."
        else:
            messages.error(request, "Action de masse inconnue.")
            return redirect(f"{reverse('users:courses')}?{self.build_query_string()}")

        messages.success(request, message)
        return redirect(f"{reverse('users:courses')}?{self.build_query_string()}")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            levels=CourseLevel.choices,
            statuses=CourseStatus.choices,
            categories=Category.objects.order_by("name"),
            selected_level=self.request.GET.get("level", ""),
            selected_category=self.request.GET.get("category", ""),
            selected_status=self.request.GET.get("status", ""),
            export_url="?" + self.build_query_string(export="csv"),
            can_bulk=self.request.user.is_staff,
        )
        return context


class CourseDetailView(EditorContextMixin, DetailView):
    model = Course
    template_name = "users/learning/course_detail.html"
    context_object_name = "course"

    def get_queryset(self):
        return (
            visible_courses(self.request.user)
            .select_related("owner", "category", "mentor__user")
            .annotate(annotated_module_count=Count("modules", distinct=True))
            .prefetch_related(
                Prefetch("modules", queryset=Module.objects.order_by("order", "id")),
                Prefetch(
                    "quizzes",
                    queryset=Quiz.objects.annotate(
                        annotated_question_count=Count("questions", distinct=True)
                    ),
                ),
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["can_edit"] = can_edit(self.request.user, self.object)
        context["can_restore"] = self.object.is_deleted and context["can_edit"]
        context["related_courses"] = (
            Course.objects.published()
            .filter(category=self.object.category)
            .exclude(pk=self.object.pk)
            .select_related("category", "owner")
            .annotate(annotated_module_count=Count("modules", distinct=True))[:3]
            if self.object.category_id
            else []
        )
        return context


class CourseCreateView(CourseCreateAccessMixin, ActionMessageMixin, PageTitleMixin,
                       FormUserMixin, CreateView):
    model = Course
    form_class = CourseForm
    template_name = "users/learning/form.html"
    page_title = "Créer un cours"
    success_message = "Le cours « {obj} » a été créé."
    error_url_name = "users:courses"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        form.instance.status = CourseStatus.DRAFT
        form.instance.is_published = False
        return super().form_valid(form)


class CourseUpdateView(CourseOwnerOrStaffMixin, ActionMessageMixin, PageTitleMixin,
                       FormUserMixin, UpdateView):
    model = Course
    form_class = CourseForm
    template_name = "users/learning/form.html"
    page_title = "Modifier le cours"
    success_message = "Le cours « {obj} » a été mis à jour."
    error_url_name = "users:course_detail"

    def form_valid(self, form):
        if not form.instance.owner_id:
            form.instance.owner = self.request.user
        return super().form_valid(form)


class CourseDeleteView(CourseOwnerOrStaffMixin, DeleteMessageMixin, DeleteView):
    model = Course
    template_name = "users/learning/confirm_delete.html"
    delete_message = "Le cours « {obj} » a été archivé."
    error_url_name = "users:course_detail"
    http_method_names = ["get", "post"]

    def get_success_url(self):
        return self.object.get_absolute_url()

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.status = CourseStatus.ARCHIVED
        self.object.is_published = False
        self.object.is_deleted = True
        self.object.save(update_fields=["status", "is_published", "is_deleted", "updated_at"])
        messages.success(request, self.delete_message.format(obj=self.object))
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_title="Archiver le cours", back_url=self.object.get_absolute_url())
        return context


class CourseRestoreView(SingleObjectMixin, CourseOwnerOrStaffMixin, View):
    model = Course
    queryset = Course.all_objects.all()
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        course = self.get_object()
        course.is_deleted = False
        course.status = CourseStatus.DRAFT
        course.is_published = False
        course.save(update_fields=["is_deleted", "status", "is_published", "updated_at"])
        messages.success(request, f"Le cours « {course} » a été restauré.")
        return redirect(course.get_absolute_url())


class CoursePublishToggleView(SingleObjectMixin, CourseOwnerOrStaffMixin, View):
    model = Course
    queryset = Course.all_objects.all()
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        course = self.get_object()
        if course.status == CourseStatus.PUBLISHED:
            course.status = CourseStatus.DRAFT
            course.is_published = False
            message = f"Le cours « {course} » n'est plus publié."
        else:
            course.status = CourseStatus.PUBLISHED
            course.is_published = True
            course.is_deleted = False
            message = f"Le cours « {course} » est maintenant publié."
        course.save(update_fields=["status", "is_published", "is_deleted", "updated_at"])
        messages.success(request, message)
        return redirect(course.get_absolute_url())


class CourseDuplicateView(SingleObjectMixin, CourseOwnerOrStaffMixin, View):
    model = Course
    queryset = Course.all_objects.all()
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        source = self.get_object()
        with transaction.atomic():
            duplicate = Course.objects.create(
                title=f"{source.title} - copie",
                short_description=source.short_description,
                description=source.description,
                level=source.level,
                category=source.category,
                duration_hours=source.duration_hours,
                owner=request.user,
                status=CourseStatus.DRAFT,
                is_published=False,
            )
            for module in source.modules.order_by("order", "id"):
                Module.objects.create(
                    course=duplicate,
                    title=module.title,
                    content=module.content,
                    order=module.order,
                )
        messages.success(request, f"Le cours « {source} » a été dupliqué.")
        return redirect(duplicate.get_absolute_url())


class ModuleListView(EditorContextMixin, FilterSortListMixin):
    model = Module
    template_name = "users/learning/module_list.html"
    context_object_name = "modules"
    search_fields = ("title", "content", "course__title")
    sort_options = {
        "ordre": ("course__title", "order", "id"),
        "titre": ("title",),
        "récent": ("-created_at",),
    }
    default_sort = "ordre"

    def get_filtered_queryset(self):
        queryset = (
            Module.objects.filter(course__in=visible_courses(self.request.user))
            .select_related("course__mentor__user")
        )
        course = self.request.GET.get("course", "")
        if course:
            queryset = queryset.filter(course_id=course)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            courses=visible_courses(self.request.user).order_by("title"),
            selected_course=self.request.GET.get("course", ""),
        )
        return context


class ModuleDetailView(EditorContextMixin, DetailView):
    model = Module
    template_name = "users/learning/module_detail.html"
    context_object_name = "module"

    def get_queryset(self):
        return (
            Module.objects.filter(course__in=visible_courses(self.request.user))
            .select_related("course__mentor__user")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["can_edit"] = can_edit(self.request.user, self.object)
        return context


class ModuleCreateView(CourseModuleCreateAccessMixin, ActionMessageMixin, PageTitleMixin,
                       FormUserMixin, CreateView):
    model = Module
    form_class = ModuleForm
    template_name = "users/learning/form.html"
    page_title = "Créer un module"
    success_message = "Le module « {obj} » a été créé."
    error_url_name = "users:module_list"

    def get_initial(self):
        initial = super().get_initial()
        course_id = self.request.GET.get("course")
        if course_id:
            initial["course"] = course_id
        return initial


class ModuleUpdateView(OwnerOrStaffMixin, ActionMessageMixin, PageTitleMixin,
                       FormUserMixin, UpdateView):
    model = Module
    form_class = ModuleForm
    template_name = "users/learning/form.html"
    page_title = "Modifier le module"
    success_message = "Le module « {obj} » a été mis à jour."
    error_url_name = "users:module_detail"


class ModuleDeleteView(OwnerOrStaffMixin, DeleteMessageMixin, DeleteView):
    model = Module
    template_name = "users/learning/confirm_delete.html"
    delete_message = "Le module « {obj} » a été supprimé."
    error_url_name = "users:module_detail"
    success_url = reverse_lazy("users:module_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_title="Supprimer le module", back_url=self.get_error_url())
        return context


class MentorListView(EditorContextMixin, FilterSortListMixin):
    model = Mentor
    template_name = "users/learning/mentor_list.html"
    context_object_name = "mentors"
    search_fields = ("user__first_name", "user__last_name", "user__email",
                     "bio", "specialties")
    sort_options = {
        "nom": ("user__first_name", "user__last_name"),
        "récent": ("-created_at",),
    }
    default_sort = "nom"

    def get_filtered_queryset(self):
        return (
            super()
            .get_filtered_queryset()
            .select_related("user")
            .annotate(annotated_course_count=Count("courses", distinct=True))
        )


class MentorDetailView(EditorContextMixin, DetailView):
    model = Mentor
    template_name = "users/learning/mentor_detail.html"
    context_object_name = "mentor"

    def get_queryset(self):
        return Mentor.objects.select_related("user").prefetch_related(
            Prefetch(
                "courses",
                queryset=Course.objects.filter(is_published=True).annotate(
                    annotated_module_count=Count("modules", distinct=True)
                ),
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["can_edit"] = can_edit(self.request.user, self.object)
        return context


class MentorCreateView(LoginRequiredMixin, ErrorRedirectMixin, UserPassesTestMixin,
                       ActionMessageMixin, PageTitleMixin, FormUserMixin, CreateView):
    """Let an authenticated user become a mentor, or a staff member create one."""

    model = Mentor
    form_class = MentorForm
    template_name = "users/learning/form.html"
    page_title = "Créer un profil mentor"
    success_message = "Le profil mentor « {obj} » a été créé."
    error_url_name = "users:mentors"

    def test_func(self):
        user = self.request.user
        return user.is_staff or not Mentor.objects.filter(user=user).exists()

    def form_valid(self, form):
        if not self.request.user.is_staff:
            form.instance.user = self.request.user
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not self.request.user.is_staff:
            context["page_title"] = "Devenir mentor"
        return context


class MentorUpdateView(OwnerOrStaffMixin, ActionMessageMixin, PageTitleMixin,
                       FormUserMixin, UpdateView):
    model = Mentor
    form_class = MentorForm
    template_name = "users/learning/form.html"
    page_title = "Modifier le profil mentor"
    success_message = "Le profil mentor « {obj} » a été mis à jour."
    error_url_name = "users:mentor_detail"


class MentorDeleteView(OwnerOrStaffMixin, DeleteMessageMixin, DeleteView):
    model = Mentor
    template_name = "users/learning/confirm_delete.html"
    delete_message = "Le profil mentor « {obj} » a été supprimé."
    error_url_name = "users:mentor_detail"
    success_url = reverse_lazy("users:mentors")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            page_title="Supprimer le profil mentor",
            back_url=self.get_error_url(),
        )
        return context


class QuizListView(EditorContextMixin, FilterSortListMixin):
    model = Quiz
    template_name = "users/learning/quiz_list.html"
    context_object_name = "quizzes"
    search_fields = ("title", "description", "course__title")
    sort_options = {
        "récent": ("-updated_at",),
        "titre": ("title",),
    }
    default_sort = "récent"

    def get_filtered_queryset(self):
        queryset = (
            Quiz.objects.filter(course__in=visible_courses(self.request.user))
            .select_related("course__mentor__user")
            .annotate(annotated_question_count=Count("questions", distinct=True))
        )
        course = self.request.GET.get("course", "")
        if course:
            queryset = queryset.filter(course_id=course)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            courses=visible_courses(self.request.user).order_by("title"),
            selected_course=self.request.GET.get("course", ""),
        )
        return context


class QuizDetailView(EditorContextMixin, DetailView):
    model = Quiz
    template_name = "users/learning/quiz_detail.html"
    context_object_name = "quiz"

    def get_queryset(self):
        return (
            Quiz.objects.filter(course__in=visible_courses(self.request.user))
            .select_related("course__mentor__user")
            .prefetch_related(
                Prefetch(
                    "questions",
                    queryset=Question.objects.prefetch_related(
                        Prefetch("answers", queryset=Answer.objects.order_by("id"))
                    ),
                )
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        questions = list(context.get("questions") or self.object.questions.all())
        context.update(
            questions=questions,
            can_edit=can_edit(self.request.user, self.object),
            total_score=sum(question.score for question in questions),
        )
        return context


class QuizCreateView(EditorRequiredMixin, ActionMessageMixin, PageTitleMixin,
                     FormUserMixin, CreateView):
    model = Quiz
    form_class = QuizForm
    template_name = "users/learning/form.html"
    page_title = "Créer un quiz"
    success_message = "Le quiz « {obj} » a été créé."
    error_url_name = "users:quiz_list"


class QuizUpdateView(OwnerOrStaffMixin, ActionMessageMixin, PageTitleMixin,
                     FormUserMixin, UpdateView):
    model = Quiz
    form_class = QuizForm
    template_name = "users/learning/form.html"
    page_title = "Modifier le quiz"
    success_message = "Le quiz « {obj} » a été mis à jour."
    error_url_name = "users:quiz_detail"


class QuizDeleteView(OwnerOrStaffMixin, DeleteMessageMixin, DeleteView):
    model = Quiz
    template_name = "users/learning/confirm_delete.html"
    delete_message = "Le quiz « {obj} » a été supprimé."
    error_url_name = "users:quiz_detail"
    success_url = reverse_lazy("users:quiz_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_title="Supprimer le quiz", back_url=self.get_error_url())
        return context


class QuestionAccessMixin(LoginRequiredMixin, ErrorRedirectMixin, UserPassesTestMixin):
    """Only staff and the owner of the related quiz may manage its questions."""

    error_url_name = "users:quiz_detail"

    def get_quiz(self) -> Quiz:
        if "quiz_id" in self.kwargs:
            return get_object_or_404(
                Quiz.objects.select_related("course__mentor__user"),
                pk=self.kwargs["quiz_id"],
            )
        return get_object_or_404(
            Quiz.objects.select_related("course__mentor__user"),
            questions__pk=self.kwargs["pk"],
        )

    def test_func(self):
        return can_edit(self.request.user, self.get_quiz())

    def get_error_url(self):
        return reverse("users:quiz_detail", args=[self.get_quiz().pk])


class QuestionCreateView(QuestionAccessMixin, ActionMessageMixin, PageTitleMixin,
                         QuestionFormSetMixin, CreateView):
    model = Question
    form_class = QuestionForm
    template_name = "users/learning/question_form.html"
    page_title = "Ajouter une question"
    success_message = "La question a été ajoutée."

    def form_valid(self, form):
        form.instance.quiz = self.get_quiz()
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["quiz"] = self.get_quiz()
        return context


class QuestionUpdateView(QuestionAccessMixin, ActionMessageMixin, PageTitleMixin,
                         QuestionFormSetMixin, UpdateView):
    model = Question
    form_class = QuestionForm
    template_name = "users/learning/question_form.html"
    page_title = "Modifier la question"
    success_message = "La question a été mise à jour."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["quiz"] = self.object.quiz
        return context


class QuestionDeleteView(QuestionAccessMixin, DeleteMessageMixin, DeleteView):
    model = Question
    template_name = "users/learning/confirm_delete.html"
    delete_message = "La question a été supprimée."

    def get_success_url(self):
        return self.object.quiz.get_absolute_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            page_title="Supprimer la question",
            back_url=self.get_success_url(),
        )
        return context
