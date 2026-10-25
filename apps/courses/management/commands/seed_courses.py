from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageDraw, ImageFont
from pymongo.errors import PyMongoError

from apps.courses import repository

COURSES = (
    {
        "title": "Python pour débutants",
        "slug": "python-pour-debutants",
        "description": "Apprenez les fondamentaux de Python avec des exercices pratiques.",
        "category": "programming",
        "level": "beginner",
        "duration_hours": 12.0,
        "price": 0.0,
        "status": "published",
    },
    {
        "title": "Concevoir une API web",
        "slug": "concevoir-une-api-web",
        "description": "Structurez une API robuste et maintenable.",
        "category": "web",
        "level": "intermediate",
        "duration_hours": 8.0,
        "price": 29.0,
        "status": "draft",
    },
    {
        "title": "Django pour développeurs",
        "slug": "django-pour-developpeurs",
        "description": "Construisez une application web complète avec Django, vues, formulaires et sécurité.",
        "category": "web",
        "level": "intermediate",
        "duration_hours": 14.0,
        "price": 49.0,
        "status": "published",
    },
    {
        "title": "Design UX et accessibilité",
        "slug": "design-ux-accessibilite",
        "description": "Concevez des interfaces lisibles, inclusives et faciles à utiliser.",
        "category": "design",
        "level": "beginner",
        "duration_hours": 9.0,
        "price": 0.0,
        "status": "published",
    },
)

PLACEHOLDER_COLORS = {
    "programming": ("#1f2140", "#ffb703"),
    "web": ("#0f3d5c", "#7dd3fc"),
    "data": ("#3b0764", "#f0abfc"),
    "design": ("#4a1d1d", "#fdba74"),
    "business": ("#0f3d2e", "#86efac"),
    "other": ("#1f2140", "#e5e7eb"),
}


def _placeholder_path(slug: str) -> str:
    return f"courses/seed-{slug}.png"


def _ensure_placeholder(slug: str, title: str, category: str) -> str:
    relative_path = _placeholder_path(slug)
    destination = Path(settings.MEDIA_ROOT) / relative_path
    if destination.exists():
        return relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    background, accent = PLACEHOLDER_COLORS.get(category, PLACEHOLDER_COLORS["other"])
    image = Image.new("RGB", (1280, 720), background)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((90, 120, 1190, 600), radius=36, fill=accent)
    draw.rounded_rectangle((150, 190, 1130, 530), radius=24, fill=background)
    try:
        font = ImageFont.load_default(size=54)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    draw.text((190, 260), title[:38], fill=accent, font=font)
    draw.text((190, 360), "SkillForge", fill="#e5e7eb", font=font)
    image.save(destination, format="PNG")
    return relative_path


def _document(course: dict, *, instructor_id: ObjectId, now: datetime) -> dict:
    price = float(course["price"])
    image = _placeholder_path(course["slug"])
    return {
        "_id": ObjectId(),
        "title": course["title"],
        "slug": course["slug"],
        "description": course["description"],
        "objectives": [],
        "category": course["category"],
        "level": course["level"],
        "duration_hours": float(course["duration_hours"]),
        "price": price,
        "is_free": price == 0,
        "language": "fr",
        "image": image,
        "image_alt": f"Illustration du cours {course['title']}",
        "thumbnail": image,
        "thumbnail_url": image,
        "status": course["status"],
        "tags": course.get("tags") or [],
        "instructor_id": instructor_id,
        "instructor_name": "SkillForge",
        "created_at": now,
        "updated_at": now,
    }


class Command(BaseCommand):
    help = "Seed the courses collection with idempotent demonstration courses."

    def handle(self, *args, **options):
        instructor_id = ObjectId("000000000000000000000001")
        now = datetime.now(timezone.utc)
        try:
            documents = []
            for course in COURSES:
                _ensure_placeholder(course["slug"], course["title"], course["category"])
                documents.append(
                    _document(course, instructor_id=instructor_id, now=now)
                )
            repository.create_indexes()
            inserted = 0
            repaired = 0
            for document in documents:
                result = repository.courses_collection.update_one(
                    {"slug": document["slug"]},
                    {"$setOnInsert": document},
                    upsert=True,
                )
                inserted += int(result.upserted_id is not None)
                if result.upserted_id is None:
                    repair = repository.courses_collection.update_one(
                        {
                            "slug": document["slug"],
                            "$or": [
                                {"image": {"$in": ["", None]}},
                                {"image": {"$exists": False}},
                            ],
                        },
                        {
                            "$set": {
                                "image": document["image"],
                                "image_alt": document["image_alt"],
                                "thumbnail": document["image"],
                                "thumbnail_url": document["image"],
                                "updated_at": now,
                            }
                        },
                    )
                    repaired += int(repair.modified_count)
        except (PyMongoError, OSError, ValueError) as error:
            raise CommandError("Could not seed MongoDB courses.") from error
        self.stdout.write(
            self.style.SUCCESS(
                f"Course seed ready ({inserted} new courses, {repaired} images repaired)."
            )
        )
