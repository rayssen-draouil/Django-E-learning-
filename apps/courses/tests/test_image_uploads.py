from __future__ import annotations

import tempfile
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image

from apps.courses import images, repository
from apps.courses.services import CourseStorageError

from .base import MongoTestCase, course_document, user_document


def png_upload(name: str = "cover.png", size: tuple[int, int] = (32, 20)) -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", size, (30, 90, 160)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def form_payload(**overrides):
    data = {
        "title": "Cours avec image",
        "slug": "",
        "description": "Description assez longue pour le formulaire de cours.",
        "objectives": "",
        "category": "web",
        "level": "beginner",
        "duration_hours": "6",
        "language": "fr",
        "is_free": "on",
        "price": "",
        "image_alt": "Un ordinateur portable sur un bureau",
        "status": "draft",
    }
    data.update(overrides)
    return data


class ImageUploadViewTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.owner = user_document(role="instructor", email="owner@example.com")

    def _authenticate(self):
        client = Client()
        session = client.session
        session["user_id"] = str(self.owner["_id"])
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch("apps.users.decorators.get_active_user", return_value=self.owner)
        )
        stack.enter_context(
            patch("apps.courses.views.get_active_user", return_value=self.owner)
        )
        return client

    def test_create_with_a_valid_image_stores_only_a_relative_path(self):
        client = self._authenticate()
        response = client.post(
            reverse("users:course_manage_create"),
            {**form_payload(), "image": png_upload()},
        )
        self.assertEqual(response.status_code, 302)
        course = repository.get_course_by_slug("cours-avec-image")
        self.assertIsNotNone(course)
        self.assertTrue(course["image"].startswith("courses/"))
        self.assertNotIn("cover.png", course["image"])
        self.assertEqual(course["image_alt"], "Un ordinateur portable sur un bureau")
        self.assertTrue((Path(settings.MEDIA_ROOT) / course["image"]).is_file())

    def test_create_without_an_image_keeps_the_field_empty(self):
        client = self._authenticate()
        response = client.post(reverse("users:course_manage_create"), form_payload())
        self.assertEqual(response.status_code, 302)
        course = repository.get_course_by_slug("cours-avec-image")
        self.assertEqual(course["image"], "")

    def test_wrong_type_oversized_and_fake_images_are_rejected(self):
        client = self._authenticate()
        create_url = reverse("users:course_manage_create")
        cases = (
            ("bad.gif", b"GIF89a", {}),
            ("fake.png", b"not really a png", {}),
            ("big.png", png_upload().read(), {"COURSE_IMAGE_MAX_BYTES": 10}),
        )
        for name, payload, overrides in cases:
            with self.subTest(name=name):
                upload = SimpleUploadedFile(name, payload, content_type="image/png")
                with override_settings(**overrides):
                    response = client.post(create_url, {**form_payload(), "image": upload})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "image")
        self.assertEqual(self.collection.count_documents({}), 0)

    def test_update_replaces_and_deletes_the_old_image(self):
        client = self._authenticate()
        client.post(
            reverse("users:course_manage_create"),
            {**form_payload(), "image": png_upload("first.png")},
        )
        course = repository.get_course_by_slug("cours-avec-image")
        old_path = Path(settings.MEDIA_ROOT) / course["image"]
        self.assertTrue(old_path.is_file())

        response = client.post(
            reverse("users:course_manage_edit", args=[str(course["_id"])]),
            {
                **form_payload(title="Cours avec image", slug="cours-avec-image"),
                "image": png_upload("second.png"),
            },
        )
        self.assertEqual(response.status_code, 302)
        updated = repository.get_course(course["_id"])
        self.assertNotEqual(updated["image"], course["image"])
        self.assertFalse(old_path.exists())
        self.assertTrue((Path(settings.MEDIA_ROOT) / updated["image"]).is_file())

    def test_update_without_a_new_image_keeps_the_current_file(self):
        client = self._authenticate()
        client.post(
            reverse("users:course_manage_create"),
            {**form_payload(), "image": png_upload()},
        )
        course = repository.get_course_by_slug("cours-avec-image")
        stored = Path(settings.MEDIA_ROOT) / course["image"]

        response = client.post(
            reverse("users:course_manage_edit", args=[str(course["_id"])]),
            {
                **form_payload(title="Titre modifié", slug="cours-avec-image"),
                "image_alt": "Nouveau texte alternatif",
            },
        )
        self.assertEqual(response.status_code, 302)
        updated = repository.get_course(course["_id"])
        self.assertEqual(updated["image"], course["image"])
        self.assertEqual(updated["image_alt"], "Nouveau texte alternatif")
        self.assertTrue(stored.is_file())

    def test_delete_removes_the_image_file(self):
        client = self._authenticate()
        client.post(
            reverse("users:course_manage_create"),
            {**form_payload(), "image": png_upload()},
        )
        course = repository.get_course_by_slug("cours-avec-image")
        stored = Path(settings.MEDIA_ROOT) / course["image"]
        self.assertTrue(stored.is_file())

        response = client.post(
            reverse("users:course_manage_delete", args=[str(course["_id"])])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(stored.exists())
        self.assertIsNone(repository.get_course(course["_id"]))

    def test_placeholder_url_and_title_alt_are_rendered_without_an_image(self):
        course = course_document(
            slug="sans-image",
            title="Cours sans image",
            status="published",
            instructor_id=self.owner["_id"],
        )
        self.collection.insert_one(course)
        response = self.client.get(reverse("users:courses"))
        self.assertContains(response, "course-placeholder.svg")
        self.assertContains(response, 'alt="Cours sans image"')

        detail = self.client.get(reverse("users:course_detail", args=["sans-image"]))
        self.assertContains(detail, "course-placeholder.svg")
        self.assertContains(detail, 'alt="Cours sans image"')

    def test_form_preview_uses_the_stored_image_and_alt(self):
        course = course_document(
            slug="avec-apercu",
            title="Cours avec aperçu",
            instructor_id=self.owner["_id"],
            image="courses/preview.png",
            image_alt="Aperçu du cours",
        )
        self.collection.insert_one(course)
        client = self._authenticate()
        response = client.get(
            reverse("users:course_manage_edit", args=[str(course["_id"])])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/media/courses/preview.png"')
        self.assertContains(response, 'alt="Aperçu du cours"')
        self.assertContains(response, 'enctype="multipart/form-data"')

    def test_image_storage_errors_are_user_friendly(self):
        client = self._authenticate()
        create_url = reverse("users:course_manage_create")
        with patch(
            "apps.courses.views.save_course_image",
            side_effect=ValidationError("Image refusée."),
        ):
            response = client.post(
                create_url, {**form_payload(), "image": png_upload()}
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Image refusée.")

        with patch("apps.courses.views.save_course_image", side_effect=OSError):
            response = client.post(
                create_url, {**form_payload(), "image": png_upload()}
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Impossible d&#x27;enregistrer l&#x27;image")
        self.assertEqual(self.collection.count_documents({}), 0)

    def test_failed_create_removes_the_uploaded_file(self):
        client = self._authenticate()
        with patch(
            "apps.courses.views.create_course", side_effect=CourseStorageError("Erreur")
        ):
            response = client.post(
                reverse("users:course_manage_create"),
                {**form_payload(), "image": png_upload()},
            )
        self.assertEqual(response.status_code, 503)
        courses_dir = Path(settings.MEDIA_ROOT) / "courses"
        self.assertEqual(list(courses_dir.glob("*")), [])

    def test_failed_update_removes_the_new_file(self):
        course = course_document(
            slug="cours-existant",
            title="Cours existant",
            instructor_id=self.owner["_id"],
        )
        self.collection.insert_one(course)
        client = self._authenticate()
        with patch(
            "apps.courses.views.update_managed_course",
            side_effect=CourseStorageError("Erreur"),
        ):
            response = client.post(
                reverse("users:course_manage_edit", args=[str(course["_id"])]),
                {**form_payload(title="Cours existant", slug="cours-existant"), "image": png_upload()},
            )
        self.assertEqual(response.status_code, 503)
        courses_dir = Path(settings.MEDIA_ROOT) / "courses"
        self.assertEqual(list(courses_dir.glob("*")), [])

    def test_invalid_object_id_on_management_routes_renders_custom_404(self):
        client = self._authenticate()
        for name in ("course_manage_detail", "course_manage_edit", "course_manage_delete"):
            with self.subTest(view=name):
                response = client.get(reverse(f"users:{name}", args=["not-an-id"]))
                self.assertEqual(response.status_code, 404)
                self.assertContains(response, "Page introuvable", status_code=404)

    def test_delete_service_removes_the_stored_file(self):
        stored = images.save_course_image(png_upload())
        course = course_document(
            slug="suppression",
            instructor_id=self.owner["_id"],
            image=stored,
        )
        self.collection.insert_one(course)
        client = self._authenticate()
        client.post(reverse("users:course_manage_delete", args=[str(course["_id"])]))
        self.assertFalse((Path(settings.MEDIA_ROOT) / stored).exists())

    def test_missing_course_renders_custom_404(self):
        client = self._authenticate()
        missing = str(ObjectId())
        response = client.get(reverse("users:course_manage_detail", args=[missing]))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page introuvable", status_code=404)
