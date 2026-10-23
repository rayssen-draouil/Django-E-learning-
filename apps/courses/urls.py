from django.urls import path

from . import views

app_name = "courses"

urlpatterns = [
    path("cours/", views.courses, name="courses"),
    path("backoffice/cours/", views.backoffice_courses, name="backoffice_courses"),
]
