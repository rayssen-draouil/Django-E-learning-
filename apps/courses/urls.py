from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "courses"

urlpatterns = [
    # Front office (read-only).
    path("cours/", views.courses, name="courses"),
    path("cours/<slug:slug>/", views.course_detail, name="course_detail"),
    path("cours/<slug:slug>/traduire/", views.course_translate, name="course_translate"),
    # Back office.
    path("backoffice/cours/", views.backoffice_courses, name="backoffice_courses"),
    path("backoffice/cours/manage/", views.manage_course_list, name="course_manage_list"),
    # Historical management URLs kept as redirects to the canonical routes.
    path(
        "backoffice/cours/nouveau/",
        RedirectView.as_view(pattern_name="users:course_manage_create", permanent=False),
        name="course_manage_create_legacy",
    ),
    path(
        "backoffice/cours/mes-cours/",
        RedirectView.as_view(pattern_name="users:course_manage_list", permanent=False),
        name="course_manage_list_legacy",
    ),
    path(
        "backoffice/cours/manage/create/",
        RedirectView.as_view(pattern_name="users:course_manage_create", permanent=False),
        name="course_manage_create_old",
    ),
    path(
        "backoffice/cours/manage/<str:course_id>/",
        RedirectView.as_view(pattern_name="users:course_manage_detail", permanent=False),
        name="course_manage_detail_old",
    ),
    path(
        "backoffice/cours/manage/<str:course_id>/edit/",
        RedirectView.as_view(pattern_name="users:course_manage_edit", permanent=False),
        name="course_manage_edit_old",
    ),
    path(
        "backoffice/cours/manage/<str:course_id>/publish/",
        RedirectView.as_view(pattern_name="users:course_manage_publish", permanent=False),
        name="course_manage_publish_old",
    ),
    path(
        "backoffice/cours/manage/<str:course_id>/delete/",
        RedirectView.as_view(pattern_name="users:course_manage_delete", permanent=False),
        name="course_manage_delete_old",
    ),
    # AI helper routes.
    path(
        "backoffice/cours/manage/ai/generate/",
        views.manage_course_ai_generate,
        name="course_manage_ai_generate",
    ),
    path(
        "backoffice/cours/manage/ai/objectives/",
        views.manage_course_ai_objectives,
        name="course_manage_ai_objectives",
    ),
    path(
        "backoffice/cours/manage/ai/improve-description/",
        views.manage_course_ai_improve_description,
        name="course_manage_ai_improve_description",
    ),
    path(
        "backoffice/cours/manage/ai/suggest-meta/",
        views.manage_course_ai_suggest_meta,
        name="course_manage_ai_suggest_meta",
    ),
    path(
        "backoffice/cours/manage/ai/status/<str:task_id>/",
        views.manage_course_ai_status,
        name="course_manage_ai_status",
    ),
    path(
        "backoffice/cours/manage/ai/report/run/",
        views.manage_course_ai_report_run,
        name="course_manage_ai_report_run",
    ),
    path(
        "backoffice/cours/manage/ai/report/status/<str:task_id>/",
        views.manage_course_ai_report_status,
        name="course_manage_ai_report_status",
    ),
    path(
        "backoffice/cours/manage/ai/report/",
        views.manage_course_ai_report,
        name="course_manage_ai_report",
    ),
    # Canonical back-office routes.
    path(
        "backoffice/cours/create/",
        views.manage_course_create,
        name="course_manage_create",
    ),
    path(
        "backoffice/cours/<str:course_id>/",
        views.manage_course_detail,
        name="course_manage_detail",
    ),
    path(
        "backoffice/cours/<str:course_id>/edit/",
        views.manage_course_edit,
        name="course_manage_edit",
    ),
    path(
        "backoffice/cours/<str:course_id>/publish/",
        views.manage_course_publish,
        name="course_manage_publish",
    ),
    path(
        "backoffice/cours/<str:course_id>/delete/",
        views.manage_course_delete,
        name="course_manage_delete",
    ),
]
