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
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile, name="profile"),
]
