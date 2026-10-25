from __future__ import annotations

import re
from contextlib import ExitStack
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from .base import MongoTestCase, course_document, user_document


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h1_count = 0
        self.images: list[str | None] = []
        self.controls: list[dict[str, str | None]] = []
        self.labels_for: set[str] = set()
        self.form_has_novalidate = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "h1":
            self.h1_count += 1
        elif tag == "img":
            self.images.append(attributes.get("alt"))
        elif tag in {"input", "select", "textarea"}:
            self.controls.append(attributes)
        elif tag == "label" and attributes.get("for"):
            self.labels_for.add(attributes["for"])
        elif tag == "form" and "novalidate" in attributes:
            self.form_has_novalidate = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def parse_page(testcase, response) -> PageParser:
    parser = PageParser()
    parser.feed(response.content.decode("utf-8"))
    testcase.assertEqual(parser.h1_count, 1)
    return parser


def assert_visible_controls_are_labelled(testcase, parser: PageParser) -> None:
    visible_controls = [
        attributes
        for attributes in parser.controls
        if attributes.get("type") != "hidden"
    ]
    for attributes in visible_controls:
        testcase.assertIn("id", attributes)
        testcase.assertIn(
            attributes["id"],
            parser.labels_for,
            f"Le champ {attributes.get('name')} n'a pas de label associé.",
        )


class PublicPageAccessibilityTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.owner = user_document(role="instructor", email="a11y@example.com")
        self.published = course_document(
            slug="python-accessible",
            title="Python accessible",
            description="Un cours publié pour vérifier l'accessibilité des pages.",
            status="published",
            instructor_id=self.owner["_id"],
            image="courses/python.png",
            image_alt="Un serpent Python posé sur un clavier",
            duration_hours=12,
            price=49,
            is_free=False,
        )
        self.collection.insert_one(self.published)

    def test_catalogue_has_one_h1_alt_text_and_labelled_filters(self):
        response = self.client.get(reverse("users:courses"))
        self.assertEqual(response.status_code, 200)
        parser = parse_page(self, response)
        self.assertTrue(parser.images)
        for alt in parser.images:
            self.assertTrue(alt, "Les images du catalogue doivent avoir un texte alternatif.")
        assert_visible_controls_are_labelled(self, parser)
        for control_id in ("course-search", "course-category", "course-level", "course-sort"):
            self.assertIn(control_id, parser.labels_for)

    def test_course_detail_has_one_h1_and_alt_text(self):
        response = self.client.get(
            reverse("users:course_detail", args=[self.published["slug"]])
        )
        self.assertEqual(response.status_code, 200)
        parser = parse_page(self, response)
        self.assertTrue(parser.images)
        for alt in parser.images:
            self.assertTrue(alt)


@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class CourseFormAccessibilityTests(SimpleTestCase):
    def setUp(self):
        self.owner = user_document(role="instructor", email="form-a11y@example.com")
        self.client = Client()
        session = self.client.session
        session["user_id"] = str(self.owner["_id"])
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch("apps.users.decorators.get_active_user", return_value=self.owner)
        )
        stack.enter_context(
            patch("apps.courses.views.get_active_user", return_value=self.owner)
        )

    def _form_response(self):
        response = self.client.get(reverse("users:course_manage_create"))
        self.assertEqual(response.status_code, 200)
        return response

    def test_form_has_one_h1_labelled_fields_and_alt_attributes(self):
        response = self._form_response()
        parser = parse_page(self, response)
        for alt in parser.images:
            self.assertIsNotNone(alt)
        assert_visible_controls_are_labelled(self, parser)
        self.assertFalse(parser.form_has_novalidate)

    def test_native_client_validation_matches_server_constraints(self):
        response = self._form_response()
        parser = parse_page(self, response)
        controls = {
            attributes.get("name"): attributes for attributes in parser.controls
        }

        self.assertIn("required", controls["title"])
        self.assertEqual(controls["title"]["minlength"], "3")
        self.assertEqual(controls["title"]["maxlength"], "160")

        self.assertIn("required", controls["description"])
        self.assertEqual(controls["description"]["maxlength"], "5000")

        self.assertEqual(controls["duration_hours"]["min"], "0")
        self.assertEqual(controls["duration_hours"]["max"], "9999.99")
        self.assertEqual(controls["price"]["min"], "0")
        self.assertEqual(controls["price"]["max"], "999999.99")

    def test_required_fields_never_show_the_optional_help_text(self):
        response = self._form_response()
        content = response.content.decode("utf-8")
        for field_name in ("title", "description", "category", "level", "status"):
            self.assertIn(
                f'id="id_{field_name}_help">Champ obligatoire.</p>',
                content,
                f"Le champ obligatoire {field_name} doit afficher « Champ obligatoire. ».",
            )
        self.assertNotIn(
            'id="id_title_help">Champ facultatif.',
            content,
        )
        self.assertNotIn(
            'id="id_description_help">Champ facultatif.',
            content,
        )


class TouchTargetCssTests(SimpleTestCase):
    def test_public_controls_have_a_44px_minimum_height(self):
        css = Path("static/frontoffice/css/courses.css").read_text(encoding="utf-8")
        patterns = (
            r"\.filter-field input, \.filter-field select \{[^}]*min-height: 2\.75rem",
            r"\.course-button \{[^}]*min-height: 2\.75rem",
            r"\.course-button\.small \{[^}]*min-height: 2\.75rem",
            r"\.site-nav a \{[^}]*min-height: 2\.75rem",
        )
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertRegex(css, re.compile(pattern, re.DOTALL))
