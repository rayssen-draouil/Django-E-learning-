from django.urls import path

from . import views

app_name = "mentors"

urlpatterns = [
    path("mentors/", views.mentors, name="mentors"),
    path("backoffice/mentors/", views.backoffice_mentors, name="backoffice_mentors"),
]
