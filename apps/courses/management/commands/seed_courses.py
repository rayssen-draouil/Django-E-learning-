from datetime import datetime, timezone

from bson import ObjectId
from django.core.management.base import BaseCommand, CommandError
from pymongo.errors import PyMongoError

from apps.courses import repository


class Command(BaseCommand):
    help = "Seed the courses collection with idempotent demonstration courses."

    def handle(self, *args, **options):
        instructor_id = ObjectId("000000000000000000000001")
        now = datetime.now(timezone.utc)
        documents = [
            {
                "title": "Python pour débutants",
                "slug": "python-pour-debutants",
                "description": "Apprenez les fondamentaux de Python avec des exercices pratiques.",
                "category": "programming",
                "level": "beginner",
                "duration_hours": 12.0,
                "price": 0.0,
                "language": "fr",
                "thumbnail_url": "",
                "thumbnail": "",
                "status": "published",
                "instructor_id": instructor_id,
                "instructor_name": "SkillForge",
                "created_at": now,
                "updated_at": now,
            },
            {
                "title": "Concevoir une API web",
                "slug": "concevoir-une-api-web",
                "description": "Structurez une API robuste et maintenable.",
                "category": "web",
                "level": "intermediate",
                "duration_hours": 8.0,
                "price": 29.0,
                "language": "fr",
                "thumbnail_url": "",
                "thumbnail": "",
                "status": "draft",
                "instructor_id": instructor_id,
                "instructor_name": "SkillForge",
                "created_at": now,
                "updated_at": now,
            },
        ]
        try:
            repository.create_indexes()
            inserted = 0
            for document in documents:
                result = repository.courses_collection.update_one(
                    {"slug": document["slug"]},
                    {"$setOnInsert": {"_id": ObjectId(), **document}},
                    upsert=True,
                )
                inserted += int(result.upserted_id is not None)
        except PyMongoError as error:
            raise CommandError("Could not seed MongoDB courses.") from error
        self.stdout.write(self.style.SUCCESS(f"Course seed ready ({inserted} new courses)."))
