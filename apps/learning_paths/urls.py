from django.urls import path

from . import views

app_name = "learning_paths"

urlpatterns = [
    path("", views.learning_path_list, name="list"),
    path("mes-parcours/", views.student_learning_path_list, name="student_list"),
    path("mes-parcours/<str:path_id>/", views.student_learning_path_detail, name="student_detail"),
    path("manage/", views.manage_learning_path_list, name="manage_list"),
    path("manage/statistics/", views.manage_learning_path_statistics, name="manage_statistics"),
    path("manage/create/", views.manage_learning_path_create, name="manage_create"),
    path("manage/generate/", views.manage_learning_path_generate, name="manage_generate"),
    path("manage/<str:path_id>/export.pdf", views.manage_export_learning_path_pdf, name="manage_export_pdf"),
    path("manage/<str:path_id>/apprenants/", views.manage_learning_path_learners, name="manage_learners"),
    path("manage/<str:path_id>/edit/", views.manage_learning_path_edit, name="manage_edit"),
    path("manage/<str:path_id>/delete/", views.manage_learning_path_delete, name="manage_delete"),
    path("<str:path_id>/inscription/", views.enroll_learning_path, name="enroll"),
    path("<str:path_id>/progression/", views.update_student_progress, name="progress"),
    path("<str:path_id>/export.pdf", views.export_published_learning_path_pdf, name="export_pdf"),
    path("<str:path_id>/", views.learning_path_detail, name="detail"),
]
