import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from pymongo.errors import PyMongoError

from apps.courses.management.commands.seed_courses import COURSES

from .base import MongoTestCase


class CreateCourseIndexesCommandTests(SimpleTestCase):
    def test_command_creates_indexes_and_reports_success(self):
        output = StringIO()
        with patch(
            "apps.courses.management.commands.create_course_indexes.repository.create_indexes"
        ) as create_indexes:
            call_command("create_course_indexes", stdout=output)
        create_indexes.assert_called_once_with()
        self.assertIn("MongoDB course indexes are ready.", output.getvalue())

    def test_command_wraps_mongo_failures(self):
        with patch(
            "apps.courses.management.commands.create_course_indexes.repository.create_indexes",
            side_effect=PyMongoError,
        ):
            with self.assertRaises(CommandError):
                call_command("create_course_indexes", stdout=StringIO())


class SeedCoursesCommandTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

    def test_seed_is_idempotent_and_creates_demo_courses(self):
        first_output = StringIO()
        call_command("seed_courses", stdout=first_output)

        documents = list(self.collection.find({}))
        self.assertEqual(len(documents), len(COURSES))
        self.assertIn(f"{len(COURSES)} new", first_output.getvalue())
        self.assertTrue(
            {"python-pour-debutants", "concevoir-une-api-web"}
            <= {document["slug"] for document in documents}
        )
        published = self.collection.find_one({"slug": "python-pour-debutants"})
        self.assertEqual(published["status"], "published")
        self.assertEqual(published["price"], 0.0)
        self.assertTrue((Path(settings.MEDIA_ROOT) / published["image"]).is_file())
        self.assertEqual(
            published["image_alt"], "Illustration du cours Python pour débutants"
        )
        draft = self.collection.find_one({"slug": "concevoir-une-api-web"})
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["price"], 29.0)

        second_output = StringIO()
        call_command("seed_courses", stdout=second_output)
        self.assertEqual(self.collection.count_documents({}), len(COURSES))
        self.assertIn("0 new", second_output.getvalue())

    def test_seed_wraps_mongo_failures(self):
        with patch(
            "apps.courses.management.commands.seed_courses.repository.create_indexes",
            side_effect=PyMongoError,
        ):
            with self.assertRaises(CommandError):
                call_command("seed_courses", stdout=StringIO())
        self.assertEqual(self.collection.count_documents({}), 0)
