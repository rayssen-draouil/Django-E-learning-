from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from apps.users.services import DuplicateEmailError, UserNotFoundError


def user_document(**overrides):
    now = datetime.now(timezone.utc)
    document = {
        "_id": ObjectId(),
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "password": make_password("A-valid-password-123!"),
        "role": "student",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    document.update(overrides)
    return document


@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class UsersViewsTests(SimpleTestCase):
    def test_legacy_route_names_and_paths_are_preserved(self):
        expected = {
            "users:courses": "/users/cours/",
            "users:mentors": "/users/mentors/",
            "users:quiz": "/users/quiz/",
            "users:backoffice": "/users/backoffice/",
            "users:backoffice_courses": "/users/backoffice/cours/",
            "users:backoffice_mentors": "/users/backoffice/mentors/",
            "users:backoffice_quizzes": "/users/backoffice/quiz/",
        }
        for route_name, path in expected.items():
            with self.subTest(route_name=route_name):
                self.assertEqual(reverse(route_name), path)

    def test_home_is_public_without_a_session(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_home_remains_public_for_authenticated_users(self):
        session = self.client.session
        session["user_id"] = str(ObjectId())
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_register_creates_session_and_never_exposes_password(self):
        created = user_document()
        with patch("apps.users.views.create_user", return_value=created) as create:
            response = self.client.post("/users/register/", {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ADA@example.com",
                "role": "student",
                "password": "A-valid-password-123!",
                "password_confirm": "A-valid-password-123!",
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/users/profile/")
        self.assertEqual(self.client.session["user_id"], str(created["_id"]))
        create.assert_called_once()

    def test_duplicate_email_is_reported_on_registration(self):
        with patch("apps.users.views.create_user", side_effect=DuplicateEmailError):
            response = self.client.post("/users/register/", {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "role": "student",
                "password": "A-valid-password-123!",
                "password_confirm": "A-valid-password-123!",
            })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "déjà utilisée")

    def test_login_accepts_valid_credentials(self):
        user = user_document()
        with patch("apps.users.views.authenticate_user", return_value=user):
            response = self.client.post("/users/login/", {
                "email": user["email"],
                "password": "A-valid-password-123!",
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/users/backoffice/")
        self.assertEqual(self.client.session["user_id"], str(user["_id"]))

    def test_invalid_login_does_not_reveal_account_existence(self):
        with patch("apps.users.views.authenticate_user", return_value=None):
            response = self.client.post("/users/login/", {
                "email": "unknown@example.com",
                "password": "wrong",
            })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Adresse e-mail ou mot de passe incorrect.")

    def test_profile_requires_authentication(self):
        response = self.client.get("/users/profile/")
        self.assertRedirects(response, "/users/login/")

    def test_profile_requires_an_active_user(self):
        self.client.session["user_id"] = str(ObjectId())
        self.client.session.save()
        with patch("apps.users.decorators.get_active_user", side_effect=UserNotFoundError):
            response = self.client.get("/users/profile/")

        self.assertRedirects(response, "/users/login/")
        self.assertNotIn("user_id", self.client.session)

    def _session_for(self, role):
        session = self.client.session
        session["user_id"] = str(ObjectId())
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        return patch("apps.users.decorators.get_active_user", return_value=user_document(role=role))

    def test_student_can_access_each_learner_dashboard(self):
        paths = ("/users/backoffice/", "/users/backoffice/cours/",
                 "/users/backoffice/mentors/")
        with self._session_for("student"), \
             patch("apps.users.views.list_published_quizzes", return_value=[]), \
             patch("apps.users.views.get_student_quiz_stats", return_value={
                 "attempts": [], "quiz_count": 0, "average_score": 0,
                 "attempt_count": 0, "pass_rate": 0,
                 "best_score": 0, "question_stats": [],
             }):
            for path in paths:
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)

    def test_visitor_is_redirected_from_learner_dashboards(self):
        paths = ("/users/backoffice/", "/users/backoffice/cours/",
                 "/users/backoffice/mentors/", "/users/backoffice/quiz/")
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertRedirects(response, "/users/login/", fetch_redirect_response=False)

    def test_instructor_and_business_admin_share_dashboard_with_role_and_quiz_management(self):
        self.assertEqual("/users/backoffice/quiz/manage/", reverse("users:quiz_manage_list"))
        for role in ("instructor", "admin"):
            with self.subTest(role=role), self._session_for(role), \
                 patch("apps.users.views.get_student_quiz_stats", return_value={
                     "attempts": [], "quiz_count": 0, "attempt_count": 0, "average_score": 0,
                     "best_score": 0, "pass_rate": 0, "question_stats": [],
                 }), \
                 patch("apps.users.views.list_published_quizzes", return_value=[]), \
                 patch("apps.users.views.list_author_quizzes", return_value=[]):
                response = self.client.get("/users/backoffice/")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Gestion des quiz")
                self.assertContains(response, "Administrateur" if role == "admin" else "Instructeur")
                legacy = self.client.get("/users/backoffice/quiz/")
                self.assertRedirects(legacy, "/users/backoffice/", fetch_redirect_response=False)

    def test_profile_is_available_to_each_active_business_role(self):
        for role in ("student", "instructor", "admin"):
            with self.subTest(role=role), self._session_for(role):
                self.assertEqual(self.client.get("/users/profile/").status_code, 200)

    def test_logout_requires_post(self):
        self.client.session["user_id"] = str(ObjectId())
        self.client.session.save()
        self.assertEqual(self.client.get("/users/logout/").status_code, 302)
        self.client.session["user_id"] = str(ObjectId())
        self.client.session.save()
        response = self.client.post("/users/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/users/login/")
        self.assertNotIn("user_id", self.client.session)
