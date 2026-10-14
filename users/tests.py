from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.test import TestCase

from .services import DuplicateEmailError, UserNotFoundError


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


class UsersViewsTests(TestCase):
    def test_home_redirects_to_login_without_a_session(self):
        response = self.client.get("/")
        self.assertRedirects(response, "/users/login/")

    def test_home_redirects_authenticated_users_to_profile(self):
        session = self.client.session
        session["user_id"] = str(ObjectId())
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/users/profile/")

    def test_register_creates_session_and_never_exposes_password(self):
        created = user_document()
        with patch("users.views.create_user", return_value=created) as create:
            response = self.client.post("/users/register/", {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ADA@example.com",
                "password": "A-valid-password-123!",
                "password_confirm": "A-valid-password-123!",
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/users/profile/")
        self.assertEqual(self.client.session["user_id"], str(created["_id"]))
        create.assert_called_once()

    def test_duplicate_email_is_reported_on_registration(self):
        with patch("users.views.create_user", side_effect=DuplicateEmailError):
            response = self.client.post("/users/register/", {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "password": "A-valid-password-123!",
                "password_confirm": "A-valid-password-123!",
            })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "déjà utilisée")

    def test_login_accepts_valid_credentials(self):
        user = user_document()
        with patch("users.views.authenticate_user", return_value=user):
            response = self.client.post("/users/login/", {
                "email": user["email"],
                "password": "A-valid-password-123!",
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/users/profile/")
        self.assertEqual(self.client.session["user_id"], str(user["_id"]))

    def test_invalid_login_does_not_reveal_account_existence(self):
        with patch("users.views.authenticate_user", return_value=None):
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
        with patch("users.decorators.get_active_user", side_effect=UserNotFoundError):
            response = self.client.get("/users/profile/")

        self.assertRedirects(response, "/users/login/")
        self.assertNotIn("user_id", self.client.session)

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
