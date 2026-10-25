from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image

from apps.courses import images


def image_bytes(fmt: str = "PNG", size: tuple[int, int] = (24, 16)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (220, 120, 20)).save(buffer, format=fmt)
    return buffer.getvalue()


def upload(name: str = "photo.png", payload: bytes | None = None) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name,
        payload if payload is not None else image_bytes(),
        content_type="image/png",
    )


@override_settings(MEDIA_URL="/media/")
class ImageValidationTests(SimpleTestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_inspect_image_accepts_real_png_and_jpeg(self):
        self.assertEqual(images.inspect_image(upload()), "PNG")
        self.assertEqual(
            images.inspect_image(upload("photo.jpg", image_bytes("JPEG"))), "JPEG"
        )

    def test_inspect_image_rejects_a_disallowed_extension(self):
        with self.assertRaises(ValidationError):
            images.inspect_image(upload("photo.gif", image_bytes("GIF")))

    def test_inspect_image_rejects_an_empty_file(self):
        with self.assertRaises(ValidationError):
            images.inspect_image(upload("empty.png", b""))

    def test_inspect_image_rejects_a_fake_image(self):
        with self.assertRaises(ValidationError):
            images.inspect_image(upload("fake.png", b"this is plain text, not a png"))

    def test_inspect_image_rejects_a_truncated_image(self):
        payload = image_bytes()
        with self.assertRaises(ValidationError):
            images.inspect_image(upload("truncated.png", payload[: len(payload) // 3]))

    def test_inspect_image_rejects_an_oversized_file(self):
        with override_settings(COURSE_IMAGE_MAX_BYTES=10):
            with self.assertRaises(ValidationError):
                images.inspect_image(upload())

    def test_save_image_generates_a_uuid_name_inside_courses(self):
        stored = images.save_course_image(upload("../../evil.png"))
        self.assertTrue(stored.startswith("courses/"))
        self.assertTrue(stored.endswith(".png"))
        self.assertNotIn("evil", stored)
        self.assertNotIn("..", stored)
        self.assertTrue((Path(settings.MEDIA_ROOT) / stored).is_file())

    def test_delete_image_removes_only_safe_course_images(self):
        stored = images.save_course_image(upload())
        images.delete_course_image(stored)
        self.assertFalse((Path(settings.MEDIA_ROOT) / stored).exists())

        images.delete_course_image("")
        images.delete_course_image(None)
        images.delete_course_image("../outside.png")
        images.delete_course_image("/etc/passwd")
        images.delete_course_image("images/course-placeholder.svg")
        images.delete_course_image("courses/missing.png")

    def test_image_url_uses_media_static_placeholder_or_external_url(self):
        self.assertEqual(
            images.course_image_url("courses/photo.png"), "/media/courses/photo.png"
        )
        self.assertEqual(
            images.course_image_url("https://cdn.example.com/photo.png"),
            "https://cdn.example.com/photo.png",
        )
        self.assertEqual(images.course_image_url("/already/absolute.png"), "/already/absolute.png")
        self.assertIn("course-placeholder.svg", images.course_image_url(""))

    def test_image_alt_falls_back_to_the_title(self):
        self.assertEqual(images.course_image_alt({"image_alt": "Une image"}), "Une image")
        self.assertEqual(images.course_image_alt({"title": "Python"}), "Python")
        self.assertEqual(images.course_image_alt({}), "Illustration du cours")
