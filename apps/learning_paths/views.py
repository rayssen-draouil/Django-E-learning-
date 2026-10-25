from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseForbidden, HttpResponseServerError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.users.decorators import roles_required
from .ai import (
    LearningPathAIError,
    answer_learning_path_question,
    generate_learning_path_proposal,
    generate_learning_path_statistics_insights,
)
from .forms import LearningPathForm
from .forms import LearningPathQuestionForm
from .options import (
    LEVEL_CHOICES,
    MANAGEMENT_SORT_CHOICES,
    PUBLIC_SORT_CHOICES,
    STATUS_FILTER_CHOICES,
    THEME_CHOICES,
)
from .pdf import build_learning_path_pdf
from .proposal_forms import LearningPathProposalForm
from .services import (
    LearningPathNotFoundError,
    LearningPathEnrollmentRequiredError,
    LearningPathPermissionError,
    LearningPathStorageError,
    create_learning_path,
    delete_learning_path,
    enroll_in_learning_path,
    get_learning_path_statistics,
    get_managed_learning_path,
    get_published_learning_path,
    get_student_learning_path,
    get_student_learning_paths,
    list_learning_path_learners,
    list_managed_learning_paths,
    list_published_learning_paths,
    set_student_course_completion,
    update_learning_path,
)


def _set_competency_status(learning_path, *, is_enrolled):
    courses = learning_path.get("courses", [])
    current_course_id = next(
        (
            course["id"]
            for course in courses
            if not course.get("is_completed", False)
        ),
        None,
    ) if is_enrolled else None

    for course in courses:
        if is_enrolled and course.get("is_completed", False):
            status = "acquired"
            label = "Acquise"
        elif is_enrolled and course["id"] == current_course_id:
            status = "in_progress"
            label = "En cours"
        else:
            status = "to_work"
            label = "À travailler"
        course["competency_status"] = status
        course["competency_status_label"] = label


def _add_competency_preview(learning_paths, *, max_shown=3):
    """Enrichit chaque parcours avec un aperçu limité de compétences pour la liste."""
    for path in learning_paths:
        all_competencies = [
            competency
            for course in path.get("courses", [])
            for competency in course.get("competencies", [])
        ]
        path["preview_competencies"] = all_competencies[:max_shown]
        path["extra_competencies_count"] = max(0, len(all_competencies) - max_shown)


@require_GET
def learning_path_list(request):
    filters = {
        "search": request.GET.get("q", "").strip()[:100],
        "theme": request.GET.get("theme", ""),
        "level": request.GET.get("level", ""),
        "sort": request.GET.get("sort", "recent"),
    }
    try:
        learning_paths = list_published_learning_paths(**filters)
    except LearningPathStorageError:
        return HttpResponseServerError("Les parcours sont temporairement indisponibles.")
    _add_competency_preview(learning_paths)
    return render(request, "frontoffice/learning_paths/list.html", {
        "learning_paths": learning_paths,
        "filters": filters,
        "theme_choices": THEME_CHOICES,
        "level_choices": LEVEL_CHOICES,
        "sort_choices": PUBLIC_SORT_CHOICES,
    })


@require_GET
def learning_path_detail(request, path_id):
    try:
        learning_path = get_published_learning_path(path_id)
    except LearningPathNotFoundError:
        raise Http404
    except LearningPathStorageError:
        return HttpResponseServerError("Les parcours sont temporairement indisponibles.")
    _set_competency_status(learning_path, is_enrolled=False)
    return render(request, "frontoffice/learning_paths/detail.html", {
        "learning_path": learning_path,
    })


@require_GET
def export_published_learning_path_pdf(request, path_id):
    try:
        learning_path = get_published_learning_path(path_id)
    except LearningPathNotFoundError:
        raise Http404
    except LearningPathStorageError:
        return HttpResponseServerError("Le parcours est temporairement indisponible.")
    return _learning_path_pdf_response(learning_path)


def _learning_path_pdf_response(learning_path, *, learners=None):
    response = HttpResponse(
        build_learning_path_pdf(learning_path, learners=learners),
        content_type="application/pdf",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="parcours-{learning_path["id"]}.pdf"'
    )
    return response


@roles_required("student")
@require_GET
def student_learning_path_list(request):
    try:
        learning_paths = get_student_learning_paths(request.mongo_user)
    except LearningPathStorageError:
        return HttpResponseServerError("Votre suivi des parcours est temporairement indisponible.")
    return render(request, "frontoffice/learning_paths/my_paths.html", {
        "learning_paths": learning_paths,
    })


@roles_required("student")
@require_http_methods(["GET", "POST"])
def student_learning_path_detail(request, path_id):
    try:
        learning_path = get_student_learning_path(path_id, request.mongo_user)
    except LearningPathNotFoundError:
        raise Http404
    except LearningPathEnrollmentRequiredError:
        messages.info(request, "Inscrivez-vous à ce parcours pour suivre votre progression.")
        return redirect("learning_paths:detail", path_id=path_id)
    except LearningPathStorageError:
        return HttpResponseServerError("Votre progression est temporairement indisponible.")
    _set_competency_status(learning_path, is_enrolled=True)
    question_form = LearningPathQuestionForm(request.POST or None)
    context = {
        "learning_path": learning_path,
        "is_enrolled": True,
        "question_form": question_form,
    }
    if request.method == "POST" and question_form.is_valid():
        try:
            context["ai_answer"] = answer_learning_path_question(
                question=question_form.cleaned_data["question"],
                learning_path=learning_path,
            )
        except LearningPathAIError as error:
            context["ai_error"] = str(error)
            return render(
                request,
                "frontoffice/learning_paths/detail.html",
                context,
                status=503,
            )
    return render(request, "frontoffice/learning_paths/detail.html", context)


@roles_required("student")
@require_POST
def enroll_learning_path(request, path_id):
    try:
        created = enroll_in_learning_path(path_id, request.mongo_user)
    except LearningPathNotFoundError:
        raise Http404
    except LearningPathStorageError:
        return HttpResponseServerError("Impossible de vous inscrire à ce parcours pour le moment.")
    if created:
        messages.success(request, "Votre inscription au parcours est enregistrée.")
    else:
        messages.info(request, "Vous êtes déjà inscrit à ce parcours.")
    return redirect("learning_paths:student_detail", path_id=path_id)


@roles_required("student")
@require_POST
def update_student_progress(request, path_id):
    completed_value = request.POST.get("completed")
    if completed_value not in {"true", "false"}:
        return HttpResponseForbidden("État de progression invalide.")
    try:
        set_student_course_completion(
            path_id,
            request.POST.get("course_id"),
            user=request.mongo_user,
            completed=completed_value == "true",
        )
    except LearningPathNotFoundError:
        raise Http404
    except LearningPathEnrollmentRequiredError:
        messages.info(request, "Inscrivez-vous à ce parcours avant de mettre à jour votre progression.")
        return redirect("learning_paths:detail", path_id=path_id)
    except LearningPathStorageError:
        return HttpResponseServerError("Impossible d'enregistrer votre progression pour le moment.")
    messages.success(request, "Votre progression a été mise à jour.")
    return redirect("learning_paths:student_detail", path_id=path_id)


@roles_required("instructor", "admin")
@require_GET
def manage_learning_path_list(request):
    filters = {
        "search": request.GET.get("q", "").strip()[:100],
        "status": request.GET.get("status", ""),
        "sort": request.GET.get("sort", "recent"),
    }
    try:
        learning_paths = list_managed_learning_paths(request.mongo_user, **filters)
    except LearningPathStorageError:
        return render(request, "backoffice/learning_paths/list.html", {
            "user": request.mongo_user,
            "learning_paths": [],
            "storage_error": True,
            "filters": filters,
            "status_choices": STATUS_FILTER_CHOICES,
            "sort_choices": MANAGEMENT_SORT_CHOICES,
        }, status=503)
    return render(request, "backoffice/learning_paths/list.html", {
        "user": request.mongo_user,
        "learning_paths": learning_paths,
        "filters": filters,
        "status_choices": STATUS_FILTER_CHOICES,
        "sort_choices": MANAGEMENT_SORT_CHOICES,
    })


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_learning_path_statistics(request):
    try:
        statistics = get_learning_path_statistics(request.mongo_user)
    except LearningPathStorageError:
        return HttpResponseServerError(
            "Les statistiques des parcours sont temporairement indisponibles."
        )

    context = {
        "user": request.mongo_user,
        "statistics": statistics,
    }
    if request.method == "POST":
        try:
            context["ai_insights"] = generate_learning_path_statistics_insights(
                statistics=statistics
            )
        except LearningPathAIError as error:
            context["ai_error"] = str(error)
            return render(
                request,
                "backoffice/learning_paths/statistics.html",
                context,
                status=503,
            )
    return render(request, "backoffice/learning_paths/statistics.html", context)


@roles_required("instructor", "admin")
@require_GET
def manage_learning_path_learners(request, path_id):
    try:
        context = list_learning_path_learners(path_id, request.mongo_user)
    except LearningPathNotFoundError:
        raise Http404
    except LearningPathPermissionError:
        return HttpResponseForbidden("Accès refusé.")
    except LearningPathStorageError:
        return HttpResponseServerError("Le suivi des apprenants est temporairement indisponible.")
    return render(request, "backoffice/learning_paths/learners.html", {
        "user": request.mongo_user,
        **context,
    })


@roles_required("instructor", "admin")
@require_GET
def manage_export_learning_path_pdf(request, path_id):
    try:
        export_context = list_learning_path_learners(path_id, request.mongo_user)
    except LearningPathNotFoundError:
        raise Http404
    except LearningPathPermissionError:
        return HttpResponseForbidden("Accès refusé.")
    except LearningPathStorageError:
        return HttpResponseServerError("Le parcours est temporairement indisponible.")
    return _learning_path_pdf_response(
        export_context["learning_path"],
        learners=export_context["learners"],
    )


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_learning_path_create(request):
    form = LearningPathForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            path_id = create_learning_path(data=form.to_document(), user=request.mongo_user)
        except LearningPathStorageError:
            return HttpResponseServerError("Impossible d'enregistrer le parcours pour le moment.")
        messages.success(request, "Le parcours a été créé.")
        return redirect("learning_paths:manage_list")
    return render(request, "backoffice/learning_paths/form.html", {
        "user": request.mongo_user,
        "form": form,
        "page_title": "Créer un parcours",
        "submit_label": "Créer le parcours",
    })


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_learning_path_generate(request):
    proposal_form = LearningPathProposalForm(request.POST or None)
    if request.method == "POST" and proposal_form.is_valid():
        try:
            proposal = generate_learning_path_proposal(**proposal_form.cleaned_data)
        except LearningPathAIError as error:
            proposal_form.add_error(None, str(error))
            return render(
                request,
                "backoffice/learning_paths/generate.html",
                {"user": request.mongo_user, "form": proposal_form},
                status=503,
            )

        initial = {
            "title": proposal["title"],
            "description": proposal["description"],
            "theme": proposal["theme"],
            "level": proposal["level"],
            "status": proposal["status"],
        }
        initial.update({
            f"course_{position}": course_id
            for position, course_id in enumerate(proposal["course_ids"], start=1)
        })
        return render(request, "backoffice/learning_paths/form.html", {
            "user": request.mongo_user,
            "form": LearningPathForm(initial=initial),
            "page_title": "Vérifier la proposition IA",
            "submit_label": "Enregistrer le parcours",
            "form_action": reverse("learning_paths:manage_create"),
            "proposal_generated": True,
        })
    return render(request, "backoffice/learning_paths/generate.html", {
        "user": request.mongo_user,
        "form": proposal_form,
    })


def _managed_path_or_response(request, path_id):
    try:
        return get_managed_learning_path(path_id, request.mongo_user), None
    except LearningPathNotFoundError:
        raise Http404
    except LearningPathPermissionError:
        return None, HttpResponseForbidden("Accès refusé.")
    except LearningPathStorageError:
        return None, HttpResponseServerError("Le stockage des parcours est temporairement indisponible.")


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_learning_path_edit(request, path_id):
    learning_path, response = _managed_path_or_response(request, path_id)
    if response:
        return response
    form = LearningPathForm(
        request.POST or None,
        learning_path=learning_path,
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_learning_path(path_id, data=form.to_document(), user=request.mongo_user)
        except LearningPathNotFoundError:
            raise Http404
        except LearningPathPermissionError:
            return HttpResponseForbidden("Accès refusé.")
        except LearningPathStorageError:
            return HttpResponseServerError("Impossible de modifier le parcours pour le moment.")
        messages.success(request, "Le parcours a été mis à jour.")
        return redirect("learning_paths:manage_list")
    return render(request, "backoffice/learning_paths/form.html", {
        "user": request.mongo_user,
        "form": form,
        "learning_path": learning_path,
        "page_title": "Modifier le parcours",
        "submit_label": "Enregistrer les modifications",
    })


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_learning_path_delete(request, path_id):
    learning_path, response = _managed_path_or_response(request, path_id)
    if response:
        return response
    if request.method == "POST":
        try:
            delete_learning_path(path_id, user=request.mongo_user)
        except LearningPathNotFoundError:
            raise Http404
        except LearningPathPermissionError:
            return HttpResponseForbidden("Accès refusé.")
        except LearningPathStorageError:
            return HttpResponseServerError("Impossible de supprimer le parcours pour le moment.")
        messages.success(request, "Le parcours a été supprimé.")
        return redirect("learning_paths:manage_list")
    return render(request, "backoffice/learning_paths/delete.html", {
        "user": request.mongo_user,
        "learning_path": learning_path,
    })
