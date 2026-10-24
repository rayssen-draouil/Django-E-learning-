from django.urls import path

from . import views

app_name = "quizzes"

urlpatterns = [
    path("quiz/", views.quiz, name="quiz"),
    path("quiz/result/<str:attempt_id>/", views.quiz_result, name="quiz_result"),
    path("quiz/<str:quiz_id>/", views.take_quiz, name="quiz_take"),
    path("backoffice/quiz/", views.backoffice_quizzes, name="backoffice_quizzes"),
    path("backoffice/quiz/manage/", views.manage_quiz_list, name="quiz_manage_list"),
    path("backoffice/quiz/manage/create/", views.manage_quiz_create, name="quiz_manage_create"),
    path("backoffice/quiz/manage/ai/generate/", views.generate_quiz_with_ai, name="quiz_ai_generate"),
    path("backoffice/quiz/manage/ai/create-draft/", views.create_ai_quiz_draft, name="quiz_ai_create_draft"),
    path("backoffice/quiz/manage/<str:quiz_id>/", views.manage_quiz_detail, name="quiz_manage_detail"),
    path("backoffice/quiz/manage/<str:quiz_id>/edit/", views.manage_quiz_edit, name="quiz_manage_edit"),
    path("backoffice/quiz/manage/<str:quiz_id>/publish/", views.manage_quiz_publish, name="quiz_manage_publish"),
    path("backoffice/quiz/manage/<str:quiz_id>/delete/", views.manage_quiz_delete, name="quiz_manage_delete"),
]
