from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from pymongo.errors import PyMongoError

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
    def test_seed_is_idempotent_and_creates_demo_courses(self):
        first_output = StringIO()
        call_command("seed_courses", stdout=first_output)

        documents = list(self.collection.find({}))
        self.assertEqual(len(documents), 2)
        self.assertIn("2 new", first_output.getvalue())
        self.assertEqual(
            {document["slug"] for document in documents},
            {"python-pour-debutants", "concevoir-une-api-web"},
        )
        published = self.collection.find_one({"slug": "python-pour-debutants"})
        self.assertEqual(published["status"], "published")
        self.assertEqual(published["price"], 0.0)
        draft = self.collection.find_one({"slug": "concevoir-une-api-web"})
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["price"], 29.0)

        second_output = StringIO()
        call_command("seed_courses", stdout=second_output)
        self.assertEqual(self.collection.count_documents({}), 2)
        self.assertIn("0 new", second_output.getvalue())

    def test_seed_wraps_mongo_failures(self):
        with patch(
            "apps.courses.management.commands.seed_courses.repository.create_indexes",
            side_effect=PyMongoError,
        ):
            with self.assertRaises(CommandError):
                call_command("seed_courses", stdout=StringIO())
        self.assertEqual(self.collection.count_documents({}), 0)
