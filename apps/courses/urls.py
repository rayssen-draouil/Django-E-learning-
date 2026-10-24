from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "courses"

urlpatterns = [
    path("cours/", views.courses, name="courses"),
    path("cours/<slug:slug>/", views.course_detail, name="course_detail"),
    path("backoffice/cours/", views.backoffice_courses, name="backoffice_courses"),
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
    path("backoffice/cours/manage/", views.manage_course_list, name="course_manage_list"),
    path(
        "backoffice/cours/manage/create/",
        views.manage_course_create,
        name="course_manage_create",
    ),
    path(
        "backoffice/cours/manage/ai/generate/",
        views.manage_course_ai_generate,
        name="course_manage_ai_generate",
    ),
    path(
        "backoffice/cours/manage/ai/status/<str:task_id>/",
        views.manage_course_ai_status,
        name="course_manage_ai_status",
    ),
    path(
        "backoffice/cours/manage/<str:course_id>/",
        views.manage_course_detail,
        name="course_manage_detail",
    ),
    path(
        "backoffice/cours/manage/<str:course_id>/edit/",
        views.manage_course_edit,
        name="course_manage_edit",
    ),
    path(
        "backoffice/cours/manage/<str:course_id>/publish/",
        views.manage_course_publish,
        name="course_manage_publish",
    ),
    path(
        "backoffice/cours/manage/<str:course_id>/delete/",
        views.manage_course_delete,
        name="course_manage_delete",
    ),
]
