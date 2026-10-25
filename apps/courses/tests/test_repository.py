from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from apps.courses import repository

from .base import MongoTestCase, course_document


class CourseRepositoryTests(MongoTestCase):
    def test_create_indexes_enforces_unique_slugs_and_exposes_named_indexes(self):
        repository.create_indexes()
        indexes = self.collection.index_information()
        for name in (
            "courses_slug_unique",
            "courses_instructor_updated",
            "courses_status_updated",
            "courses_category",
            "courses_text",
        ):
            self.assertIn(name, indexes)
        self.assertTrue(indexes["courses_slug_unique"].get("unique"))
        self.collection.insert_one(course_document(slug="python"))
        with self.assertRaises(DuplicateKeyError):
            self.collection.insert_one(course_document(slug="python"))

    def test_crud_operations_and_slug_lookup(self):
        document = course_document(slug="python", title="Python")
        inserted_id = repository.insert_course(document)
        self.assertEqual(inserted_id, document["_id"])
        self.assertEqual(repository.get_course(document["_id"])["title"], "Python")
        self.assertEqual(repository.get_course_by_slug("python")["_id"], document["_id"])
        self.assertTrue(repository.slug_exists("python"))
        self.assertFalse(repository.slug_exists("python", exclude_id=document["_id"]))

        self.assertTrue(repository.update_course(document["_id"], {"title": "Python avancé"}))
        self.assertEqual(repository.get_course(document["_id"])["title"], "Python avancé")
        self.assertFalse(repository.update_course(ObjectId(), {"title": "Absent"}))

        self.assertTrue(repository.delete_course(document["_id"]))
        self.assertFalse(repository.delete_course(document["_id"]))
        self.assertIsNone(repository.get_course(document["_id"]))

    def test_list_and_count_support_filters_search_and_pagination(self):
        self.collection.insert_many([
            course_document(slug="python-1", title="Python débutant", status="published",
                            category="programming", level="beginner", instructor_id=ObjectId()),
            course_document(slug="python-2", title="Python avancé", status="published",
                            category="programming", level="advanced", instructor_id=ObjectId()),
            course_document(slug="design-1", title="Design UX", status="published",
                            category="design", level="beginner", instructor_id=ObjectId()),
            course_document(slug="draft-1", title="Python brouillon", status="draft",
                            category="programming", level="beginner", instructor_id=ObjectId()),
        ])
        instructor_id = self.collection.find_one({"slug": "python-1"})["instructor_id"]

        published = repository.list_courses(published_only=True)
        self.assertEqual(len(published), 3)
        self.assertTrue(all(item["status"] == "published" for item in published))

        self.assertEqual(repository.count_courses(published_only=True), 3)
        self.assertEqual(repository.count_courses(search="python", published_only=True), 2)
        self.assertEqual(
            repository.count_courses(category="programming", level="advanced", published_only=True),
            1,
        )
        self.assertEqual(repository.list_courses(status="draft")[0]["slug"], "draft-1")
        self.assertEqual(repository.list_courses(instructor_id=instructor_id)[0]["slug"], "python-1")

        page_one = repository.list_courses(published_only=True, skip=0, limit=2)
        page_two = repository.list_courses(published_only=True, skip=2, limit=2)
        self.assertEqual(len(page_one), 2)
        self.assertEqual(len(page_two), 1)
        self.assertFalse({item["_id"] for item in page_one} & {item["_id"] for item in page_two})

    def test_public_sort_orders_by_recent_title_price_and_duration(self):
        now = datetime.now(timezone.utc)
        self.collection.insert_many([
            course_document(
                slug="alpha", title="Alpha", status="published", price=30,
                duration_hours=5, updated_at=now - timedelta(days=2),
            ),
            course_document(
                slug="beta", title="Beta", status="published", price=10,
                duration_hours=20, updated_at=now,
            ),
            course_document(
                slug="gamma", title="Gamma", status="published", price=20,
                duration_hours=10, updated_at=now - timedelta(days=1),
            ),
        ])

        def titles(sort_by):
            return [
                item["title"]
                for item in repository.list_courses(published_only=True, sort_by=sort_by)
            ]

        self.assertEqual(titles("recent"), ["Beta", "Gamma", "Alpha"])
        self.assertEqual(titles("title"), ["Alpha", "Beta", "Gamma"])
        self.assertEqual(titles("price_asc"), ["Beta", "Gamma", "Alpha"])
        self.assertEqual(titles("duration_asc"), ["Alpha", "Gamma", "Beta"])
        self.assertEqual(titles("unknown"), ["Beta", "Gamma", "Alpha"])

    def test_search_matches_tags_and_rejects_nosql_injection(self):
        self.collection.insert_one(
            course_document(
                slug="tagged-course",
                title="Formation Complète",
                status="published",
                tags=["django", "mongodb"],
            )
        )
        self.assertEqual(repository.count_courses(search="mongodb", published_only=True), 1)
        # Injection payload rejected safely (returns 0 matches)
        self.assertEqual(repository.count_courses(search={"$ne": ""}, published_only=True), 0)
        self.assertEqual(repository.count_courses(search="$where", published_only=True), 0)
        self.assertIsNone(repository.get_course_by_slug("$gt"))
        self.assertFalse(repository.slug_exists("$where"))
