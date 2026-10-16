from django.urls import path
from django.views.generic import RedirectView

from . import content_views as lc
from . import views

app_name = "users"

urlpatterns = [
    # Courses
    path("cours/", lc.CourseListView.as_view(), name="courses"),
    path(
        "cours/nouveau/",
        RedirectView.as_view(pattern_name="users:course_create", permanent=False),
    ),
    path(
        "cours/create/",
        RedirectView.as_view(pattern_name="users:course_create", permanent=False),
    ),
    path("cours/<slug:slug>/", lc.CourseDetailView.as_view(), name="course_detail"),
    path("cours/<slug:slug>/modifier/", lc.CourseUpdateView.as_view(), name="course_update"),
    path("cours/<slug:slug>/supprimer/", lc.CourseDeleteView.as_view(), name="course_delete"),
    path("cours/<slug:slug>/restaurer/", lc.CourseRestoreView.as_view(), name="course_restore"),
    path("cours/<slug:slug>/dupliquer/", lc.CourseDuplicateView.as_view(), name="course_duplicate"),
    path(
        "cours/<slug:slug>/publier-ou-depublier/",
        lc.CoursePublishToggleView.as_view(),
        name="course_toggle_publish",
    ),
    # Modules
    path("modules/", lc.ModuleListView.as_view(), name="module_list"),
    path("modules/creer/", lc.ModuleCreateView.as_view(), name="module_create"),
    path("modules/<int:pk>/", lc.ModuleDetailView.as_view(), name="module_detail"),
    path("modules/<int:pk>/modifier/", lc.ModuleUpdateView.as_view(), name="module_update"),
    path("modules/<int:pk>/supprimer/", lc.ModuleDeleteView.as_view(), name="module_delete"),
    # Mentors
    path("mentors/", lc.MentorListView.as_view(), name="mentors"),
    path("mentors/creer/", lc.MentorCreateView.as_view(), name="mentor_create"),
    path("mentors/<int:pk>/", lc.MentorDetailView.as_view(), name="mentor_detail"),
    path("mentors/<int:pk>/modifier/", lc.MentorUpdateView.as_view(), name="mentor_update"),
    path("mentors/<int:pk>/supprimer/", lc.MentorDeleteView.as_view(), name="mentor_delete"),
    # Quizzes
    path("quiz/", lc.QuizListView.as_view(), name="quiz"),
    path("quiz/creer/", lc.QuizCreateView.as_view(), name="quiz_create"),
    path("quiz/<int:pk>/", lc.QuizDetailView.as_view(), name="quiz_detail"),
    path("quiz/<int:pk>/modifier/", lc.QuizUpdateView.as_view(), name="quiz_update"),
    path("quiz/<int:pk>/supprimer/", lc.QuizDeleteView.as_view(), name="quiz_delete"),
    # Questions and answers
    path(
        "quiz/<int:quiz_id>/questions/creer/",
        lc.QuestionCreateView.as_view(),
        name="question_create",
    ),
    path(
        "questions/<int:pk>/modifier/",
        lc.QuestionUpdateView.as_view(),
        name="question_update",
    ),
    path(
        "questions/<int:pk>/supprimer/",
        lc.QuestionDeleteView.as_view(),
        name="question_delete",
    ),
    # Dashboards and account
    path("backoffice/", views.backoffice, name="backoffice"),
    path("backoffice/cours/", views.backoffice_courses, name="backoffice_courses"),
    path("backoffice/cours/nouveau/", lc.CourseCreateView.as_view(), name="course_create"),
    path("backoffice/mentors/", views.backoffice_mentors, name="backoffice_mentors"),
    path("backoffice/quiz/", views.backoffice_quizzes, name="backoffice_quizzes"),
    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile, name="profile"),
]
