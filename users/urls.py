from django.urls import path

from . import views

app_name = "users"

urlpatterns = [
    path("cours/", views.courses, name="courses"),
    path("mentors/", views.mentors, name="mentors"),
    path("quiz/", views.quiz, name="quiz"),
    path("backoffice/", views.backoffice, name="backoffice"),
    path("backoffice/cours/", views.backoffice_courses, name="backoffice_courses"),
    path("backoffice/mentors/", views.backoffice_mentors, name="backoffice_mentors"),
    path("backoffice/quiz/", views.backoffice_quizzes, name="backoffice_quizzes"),
    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile, name="profile"),
]
