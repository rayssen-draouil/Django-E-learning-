from django.urls import path

from apps.courses.urls import urlpatterns as course_urlpatterns
from apps.mentors.urls import urlpatterns as mentor_urlpatterns
from apps.quizzes.urls import urlpatterns as quiz_urlpatterns
from . import views

app_name = "users"

urlpatterns = [
    *course_urlpatterns,
    *mentor_urlpatterns,
    *quiz_urlpatterns,
    path("backoffice/", views.backoffice, name="backoffice"),
    path("register/", views.register, name="register"),
    path("forgot-password/", views.forgot_password, name="forgot_password"),
    path("reset-password/<str:token>/", views.reset_password, name="reset_password"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("backoffice/utilisateurs/", views.admin_users, name="admin_users"),
    path("profile/", views.profile, name="profile"),
]
