from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase
from pymongo.errors import PyMongoError

from apps.courses.forms import CourseForm


def form_data(**overrides):
    data = {
        "title": "Python pour débutants",
        "slug": "",
        "description": "Apprenez les bases du langage Python avec des exercices.",
        "objectives": "",
        "category": "programming",
        "level": "beginner",
        "is_free": "on",
        "price": "",
        "image_alt": "Un serpent Python sur un clavier",
        "status": "draft",
    }
    data.update(overrides)
    return data


class CourseFormTests(SimpleTestCase):
    def test_valid_free_course_clears_price_and_keeps_slug_blank(self):
        form = CourseForm(form_data())
        self.assertTrue(form.is_valid(), form.errors)
        document = form.to_document()
        self.assertEqual(document["price"], 0.0)
        self.assertTrue(document["is_free"])
        self.assertEqual(document["slug"], "")
        self.assertEqual(document["image_alt"], "Un serpent Python sur un clavier")
        self.assertNotIn("image", document)

    def test_paid_course_accepts_a_positive_price(self):
        form = CourseForm(form_data(is_free="", price="19.99"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.to_document()["is_free"])
        self.assertEqual(form.to_document()["price"], 19.99)

    def test_paid_course_requires_a_price(self):
        form = CourseForm(form_data(is_free="", price=""))
        self.assertFalse(form.is_valid())
        self.assertIn("price", form.errors)

    def test_rejects_negative_price(self):
        form = CourseForm(form_data(is_free="", price="-1"))
        self.assertFalse(form.is_valid())
        self.assertIn("price", form.errors)

    def test_rejects_invalid_choices_and_oversized_text(self):
        cases = (
            form_data(category="unknown"),
            form_data(level="expert"),
            form_data(status="archived"),
            form_data(title="x" * 161),
            form_data(title="ab"),
            form_data(description="x" * 5001),
            form_data(image_alt="x" * 161),
        )
        for data in cases:
            with self.subTest(data=data):
                self.assertFalse(CourseForm(data).is_valid())

    def test_rejects_an_explicit_slug_longer_than_the_limit(self):
        form = CourseForm(form_data(slug="a" * 181))
        self.assertFalse(form.is_valid())
        self.assertIn("slug", form.errors)

    def test_normalizes_an_explicit_slug_and_rejects_a_duplicate(self):
        with patch("apps.courses.forms.repository.slug_exists", return_value=False):
            form = CourseForm(form_data(slug="  Mon Super Cours !  "))
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data["slug"], "mon-super-cours")

        with patch("apps.courses.forms.repository.slug_exists", return_value=True):
            form = CourseForm(form_data(slug="mon-super-cours"))
            self.assertFalse(form.is_valid())
            self.assertIn("slug", form.errors)

    def test_slug_uniqueness_check_ignores_the_current_course(self):
        with patch(
            "apps.courses.forms.repository.slug_exists", return_value=False
        ) as slug_exists:
            form = CourseForm(form_data(slug="python"), course={"_id": "course-id"})
            self.assertTrue(form.is_valid(), form.errors)
            slug_exists.assert_called_once_with("python", exclude_id="course-id")

    def test_slug_check_reports_a_storage_failure(self):
        with patch("apps.courses.forms.repository.slug_exists", side_effect=PyMongoError):
            form = CourseForm(form_data(slug="python"))
            self.assertFalse(form.is_valid())
            self.assertIn("Impossible de vérifier", form.errors["slug"][0])

    def test_edit_form_prefills_existing_course_values(self):
        course = {
            "_id": "course-id",
            "title": "Cours existant",
            "slug": "cours-existant",
            "description": "Description existante.",
            "objectives": ["Objectif un", "Objectif deux"],
            "category": "web",
            "level": "advanced",
            "is_free": False,
            "price": 49.5,
            "image": "courses/existing.png",
            "image_alt": "Une illustration existante",
            "status": "published",
            "tags": ["django", "backend"],
        }
        form = CourseForm(None, course=course)
        self.assertFalse(form.is_bound)
        self.assertEqual(form["title"].value(), "Cours existant")
        self.assertEqual(form["slug"].value(), "cours-existant")
        self.assertEqual(form["price"].value(), 49.5)
        self.assertEqual(form["objectives"].value(), "Objectif un\nObjectif deux")
        self.assertEqual(form["category"].value(), "web")
        self.assertEqual(form["tags"].value(), "django, backend")
        self.assertEqual(form["image_alt"].value(), "Une illustration existante")
        self.assertEqual(form["status"].value(), "published")

    def test_invalid_fields_are_linked_to_their_help_text_and_errors(self):
        form = CourseForm(form_data(title=""))
        self.assertFalse(form.is_valid())
        self.assertEqual(form["title"].field.widget.attrs["aria-invalid"], "true")
        described_by = form["title"].field.widget.attrs["aria-describedby"]
        self.assertIn("id_title_help", described_by)
        self.assertIn("id_title_error", described_by)

    def test_objectives_are_normalized_and_blank_lines_removed(self):
        form = CourseForm(form_data(objectives="  Première   étape \n\n Deuxième étape  "))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.to_document()["objectives"], ["Première étape", "Deuxième étape"])

    def test_clean_trims_title_and_description(self):
        form = CourseForm(form_data(title="  Python   avancé  ", description="  Une description.  "))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["title"], "Python avancé")
        self.assertEqual(form.cleaned_data["description"], "Une description.")

    def test_tags_are_sanitized_and_stored_as_a_list(self):
        form = CourseForm(form_data(tags=" Django, <b>Python</b>, django, data science "))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.to_document()["tags"], ["django", "python", "data science"])

    def test_tags_enforce_count_and_per_tag_length(self):
        too_many = ", ".join(f"tag{i}" for i in range(9))
        self.assertFalse(CourseForm(form_data(tags=too_many)).is_valid())
        self.assertFalse(CourseForm(form_data(tags="x" * 31)).is_valid())
