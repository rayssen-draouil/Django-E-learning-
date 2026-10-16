from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .management.commands.seed_demo import COURSES
from .models import Course, Module, CourseStatus


class SeedDemoTests(TestCase):
    def test_seed_creates_eight_courses_without_images(self):
        call_command("seed_demo", stdout=StringIO())

        self.assertEqual(Course.objects.count(), len(COURSES))
        expected_modules = sum(len(item["modules"]) for item in COURSES)
        self.assertEqual(Module.objects.count(), expected_modules)
        for course in Course.objects.all():
            self.assertFalse(course.image)
            self.assertEqual(course.status, CourseStatus.PUBLISHED)
            self.assertIsNotNone(course.category)
            self.assertIsNotNone(course.owner)
            self.assertGreaterEqual(course.modules.count(), 4)
            self.assertLessEqual(course.modules.count(), 5)

    def test_modules_are_ordered_from_one(self):
        call_command("seed_demo", stdout=StringIO())

        course = Course.objects.first()
        orders = list(course.modules.values_list("order", flat=True))
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(orders[0], 1)

    def test_seed_is_idempotent(self):
        call_command("seed_demo", stdout=StringIO())
        course_count = Course.objects.count()
        module_count = Module.objects.count()

        call_command("seed_demo", stdout=StringIO())

        self.assertEqual(Course.objects.count(), course_count)
        self.assertEqual(Module.objects.count(), module_count)
