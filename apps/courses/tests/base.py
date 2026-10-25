from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from django.conf import settings
from django.test import TestCase
from pymongo import MongoClient

from apps.courses import ai, repository, tasks

TEST_DATABASE_NAME = "e_learning_test"


def course_document(**overrides):
    now = datetime.now(timezone.utc)
    document = {
        "_id": ObjectId(),
        "title": "Cours de test",
        "slug": "cours-de-test",
        "description": "Un cours utilisé par les tests automatisés.",
        "objectives": ["Comprendre les bases", "Pratiquer avec des exercices"],
        "category": "programming",
        "level": "beginner",
        "duration_hours": 10.0,
        "price": 0.0,
        "language": "fr",
        "is_free": True,
        "image": "",
        "image_alt": "",
        "thumbnail": "",
        "thumbnail_url": "",
        "status": "published",
        "tags": [],
        "instructor_id": ObjectId(),
        "instructor_name": "Ada Lovelace",
        "created_at": now,
        "updated_at": now,
    }
    document.update(overrides)
    return document


def user_document(**overrides):
    now = datetime.now(timezone.utc)
    document = {
        "_id": ObjectId(),
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "role": "instructor",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    document.update(overrides)
    return document


class MongoTestCase(TestCase):
    """Run against a dedicated MongoDB database and clean it between tests."""

    databases: set[str] = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mongo_client = MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=5000)
        cls.mongo_db = cls.mongo_client[TEST_DATABASE_NAME]
        cls.collection = cls.mongo_db["courses"]
        cls.original_collection = repository.courses_collection
        repository.courses_collection = cls.collection
        cls.ai_collection = cls.mongo_db["ai_logs"]
        cls.original_ai_collection = ai.ai_logs_collection
        ai.ai_logs_collection = cls.ai_collection
        cls.ai_tasks_collection = cls.mongo_db["course_ai_tasks"]
        cls.original_ai_tasks_collection = tasks.ai_tasks_collection
        tasks.ai_tasks_collection = cls.ai_tasks_collection
        cls.translations_collection = cls.mongo_db["course_translations"]
        cls.original_translations_collection = repository.course_translations_collection
        repository.course_translations_collection = cls.translations_collection

    @classmethod
    def tearDownClass(cls):
        try:
            cls.mongo_client.drop_database(TEST_DATABASE_NAME)
        finally:
            repository.courses_collection = cls.original_collection
            ai.ai_logs_collection = cls.original_ai_collection
            tasks.ai_tasks_collection = cls.original_ai_tasks_collection
            repository.course_translations_collection = cls.original_translations_collection
            cls.mongo_client.close()
            super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.collection.delete_many({})
        self.ai_collection.delete_many({})
        self.ai_tasks_collection.delete_many({})
        self.translations_collection.delete_many({})

    def tearDown(self):
        self.collection.delete_many({})
        self.ai_collection.delete_many({})
        self.ai_tasks_collection.delete_many({})
        self.translations_collection.delete_many({})
        super().tearDown()
