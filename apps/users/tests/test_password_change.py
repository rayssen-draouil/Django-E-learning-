from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from apps.users.forms import ChangePasswordForm
from apps.users.services import (
    PasswordChangeError,
    UserNotFoundError,
    UserStorageError,
    change_password,
)


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


class ChangePasswordFormTests(SimpleTestCase):
    def test_the_form_requires_matching_confirmation(self):
        form = ChangePasswordForm({"current_password": "Ancien-1!", "new_password": "Nouveau-Mot-12!",
                                   "new_password_confirm": "Autre-2!"})
        self.assertFalse(form.is_valid())
        self.assertIn("new_password_confirm", form.errors)

    def test_the_form_accepts_a_strong_new_password(self):
        form = ChangePasswordForm({"current_password": "Ancien-1!", "new_password": "Nouveau-Mot-12!",
                                   "new_password_confirm": "Nouveau-Mot-12!"})
        self.assertTrue(form.is_valid(), form.errors)

    def test_the_form_rejects_a_weak_password(self):
        form = ChangePasswordForm({"current_password": "Ancien-1!", "new_password": "court",
                                   "new_password_confirm": "court"})
        self.assertFalse(form.is_valid())


class ChangePasswordServiceTests(SimpleTestCase):
    utilisateur = None

    def setUp(self):
        self.utilisateur = user_document()

    def test_the_current_password_is_verified_before_the_change(self):
        with patch("apps.users.repositories.find_user", return_value=self.utilisateur), \
             patch("apps.users.repositories.update_password") as update:
            with self.assertRaises(PasswordChangeError):
                change_password(str(self.utilisateur["_id"]), current_password="Mauvais-1!",
                                new_password="Nouveau-Mot-12!")
        update.assert_not_called()

    def test_the_change_updates_the_password_and_cuts_pending_resets(self):
        with patch("apps.users.repositories.find_user", return_value=self.utilisateur), \
             patch("apps.users.repositories.update_password", return_value=True) as update, \
             patch("apps.users.repositories.invalidate_password_resets") as invalidate:
            change_password(str(self.utilisateur["_id"]), current_password="A-valid-password-123!",
                            new_password="Nouveau-Mot-12!")
        update.assert_called_once()
        self.assertEqual(update.call_args[0][0], self.utilisateur["_id"])
        invalidate.assert_called_once_with(self.utilisateur["_id"])

    def test_a_missing_user_is_reported(self):
        with patch("apps.users.repositories.find_user", return_value=None):
            with self.assertRaises(UserNotFoundError):
                change_password(str(self.utilisateur["_id"]), current_password="x",
                                new_password="Nouveau-Mot-12!")

    def test_a_storage_outage_is_reported(self):
        from pymongo.errors import PyMongoError
        with patch("apps.users.repositories.find_user", side_effect=PyMongoError("panne")):
            with self.assertRaises(UserStorageError):
                change_password(str(self.utilisateur["_id"]), current_password="x",
                                new_password="Nouveau-Mot-12!")


@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class ProfilePasswordFlowTests(SimpleTestCase):
    def setUp(self):
        from django.test import Client
        self.client = Client()
        self.utilisateur = user_document()

    def _session(self):
        session = self.client.session
        session["user_id"] = str(self.utilisateur["_id"])
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        return patch("apps.users.decorators.get_active_user", return_value=self.utilisateur)

    def _donnees_valides(self):
        return {
            "action": "password",
            "current_password": "A-valid-password-123!",
            "new_password": "Nouveau-Mot-12!",
            "new_password_confirm": "Nouveau-Mot-12!",
        }

    def test_the_profile_page_offers_the_password_form(self):
        with self._session():
            reponse = self.client.get(reverse("users:profile"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Changer le mot de passe")
        self.assertContains(reponse, "Mot de passe actuel")

    def test_a_student_updates_their_password_from_the_profile(self):
        with self._session(), \
             patch("apps.users.views.change_password") as changement:
            reponse = self.client.post(reverse("users:profile"), self._donnees_valides())
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(reponse["Location"], reverse("users:profile"))
        changement.assert_called_once_with(
            str(self.utilisateur["_id"]),
            current_password="A-valid-password-123!",
            new_password="Nouveau-Mot-12!",
        )

    def test_a_wrong_current_password_keeps_the_user_on_the_page(self):
        with self._session(), \
             patch("apps.users.views.change_password", side_effect=PasswordChangeError("incorrect")):
            reponse = self.client.post(reverse("users:profile"), self._donnees_valides())
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Le mot de passe actuel est incorrect.")

    def test_a_storage_outage_is_reported_without_leaking_details(self):
        with self._session(), \
             patch("apps.users.views.change_password", side_effect=UserStorageError("panne")):
            reponse = self.client.post(reverse("users:profile"), self._donnees_valides())
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Modification impossible pour le moment.")

    def test_a_mismatched_confirmation_is_reported(self):
        donnees = dict(self._donnees_valides(), new_password_confirm="Autre-2!")
        with self._session(), \
             patch("apps.users.views.change_password") as changement:
            reponse = self.client.post(reverse("users:profile"), donnees)
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Les mots de passe ne correspondent pas.")
        changement.assert_not_called()
