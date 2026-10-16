from django.contrib import admin

from .models import Answer, Category, Course, CourseStatus, Mentor, Module, Question, Quiz


class ModuleInline(admin.TabularInline):
    model = Module
    extra = 0


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0


class AnswerInline(admin.TabularInline):
    model = Answer
    extra = 0


@admin.register(Mentor)
class MentorAdmin(admin.ModelAdmin):
    list_display = ("display_name", "email", "specialties", "created_at")
    search_fields = ("user__first_name", "user__last_name", "user__email", "specialties")
    list_filter = ("created_at",)
    raw_id_fields = ("user",)

    @admin.display(ordering="user__email", description="e-mail")
    def email(self, mentor):
        return mentor.user.email


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "level", "status", "owner", "updated_at")
    list_filter = ("category", "level", "status", "is_deleted", "created_at")
    search_fields = ("title", "slug", "short_description", "description", "owner__email")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("category", "owner")
    inlines = (ModuleInline,)
    date_hierarchy = "created_at"
    actions = ("publish_courses", "archive_courses")

    def get_queryset(self, request):
        return Course.all_objects.select_related("category", "owner")

    @admin.action(description="Publier les cours sélectionnés")
    def publish_courses(self, request, queryset):
        queryset.update(status=CourseStatus.PUBLISHED, is_deleted=False, is_published=True)

    @admin.action(description="Archiver les cours sélectionnés")
    def archive_courses(self, request, queryset):
        queryset.update(status=CourseStatus.ARCHIVED, is_published=False)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "updated_at")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("name",)


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "order", "updated_at")
    list_filter = ("course",)
    search_fields = ("title", "content", "course__title")
    list_editable = ("order",)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("text", "quiz", "score", "order")
    list_filter = ("quiz",)
    search_fields = ("text", "quiz__title")
    inlines = (AnswerInline,)


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "is_published", "updated_at")
    list_filter = ("is_published", "course")
    search_fields = ("title", "description")
    list_editable = ("is_published",)
    inlines = (QuestionInline,)


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ("text", "question", "is_correct")
    list_filter = ("is_correct",)
    search_fields = ("text", "question__text")
