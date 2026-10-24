from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError

from config.mongodb import db

logger = logging.getLogger(__name__)

reviews_collection: Collection[dict[str, Any]] = db["mentor_reviews"]


class ReviewError(Exception):
    """Base error for review operations."""


class ReviewValidationError(ReviewError):
    """Raised when review data fails validation."""


class ReviewDuplicateError(ReviewError):
    """Raised when a review already exists for this booking."""


class ReviewPermissionError(ReviewError):
    """Raised when user is unauthorized to submit or view this review."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_object_id(value: Any) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    raise ReviewValidationError(f"Invalid identifier: {value}")


def create_indexes() -> None:
    try:
        reviews_collection.create_index(
            [("booking_id", 1)],
            unique=True,
            name="mentor_reviews_booking_unique",
        )
        reviews_collection.create_index(
            [("mentor_id", 1), ("created_at", -1)],
            name="mentor_reviews_mentor_recent",
        )
        reviews_collection.create_index(
            [("student_id", 1), ("created_at", -1)],
            name="mentor_reviews_student_recent",
        )
    except PyMongoError:
        logger.warning("Could not create mentor_reviews indexes.")


def create_review(
    *,
    booking: dict[str, Any],
    student_user: dict[str, Any],
    rating: int | str,
    review_text: str,
) -> ObjectId:
    if not isinstance(student_user, dict):
        raise ReviewPermissionError("Authentication required.")
    student_id = parse_object_id(student_user.get("_id"))

    # Verify student owns the booking
    booking_student_id = parse_object_id(booking.get("student_id"))
    if student_id != booking_student_id:
        raise ReviewPermissionError("You can only review sessions you participated in as a student.")

    # Verify mentor is not rating themselves
    mentor_id = parse_object_id(booking.get("mentor_id"))
    if student_id == mentor_id:
        raise ReviewPermissionError("Mentors cannot review their own coaching sessions.")

    # Verify booking status is completed
    if booking.get("status") != "completed":
        raise ReviewValidationError(
            "Reviews can only be submitted for completed mentoring sessions."
        )

    # Validate rating
    try:
        rating_int = int(rating)
        if rating_int < 1 or rating_int > 5:
            raise ValueError
    except (TypeError, ValueError):
        raise ReviewValidationError("Rating must be an integer between 1 and 5 stars.")

    cleaned_text = (review_text or "").strip()
    if len(cleaned_text) < 10:
        raise ReviewValidationError("Please provide at least 10 characters of constructive feedback.")
    if len(cleaned_text) > 1000:
        raise ReviewValidationError("Review text must be 1000 characters or fewer.")

    booking_id = parse_object_id(booking.get("_id"))
    student_name = f"{student_user.get('first_name', '')} {student_user.get('last_name', '')}".strip() or "Student"

    now = _now()
    doc: dict[str, Any] = {
        "_id": ObjectId(),
        "booking_id": booking_id,
        "student_id": student_id,
        "student_name": student_name,
        "mentor_id": mentor_id,
        "rating": rating_int,
        "review_text": cleaned_text,
        "created_at": now,
        "updated_at": now,
    }

    try:
        reviews_collection.insert_one(doc)
        return doc["_id"]
    except DuplicateKeyError as error:
        raise ReviewDuplicateError("A review has already been submitted for this session.") from error
    except PyMongoError as error:
        logger.error("Failed to insert review for booking %s: %s", booking_id, error)
        raise ReviewError("Could not save review. Please try again.") from error


def get_review_by_booking(booking_id: Any) -> dict[str, Any] | None:
    try:
        bid = parse_object_id(booking_id)
        doc = reviews_collection.find_one({"booking_id": bid})
        if doc:
            doc["id"] = str(doc["_id"])
        return doc
    except Exception:
        return None


def list_mentor_reviews(mentor_id: Any, limit: int = 50) -> list[dict[str, Any]]:
    try:
        mid = parse_object_id(mentor_id)
        cursor = reviews_collection.find({"mentor_id": mid}).sort("created_at", -1).limit(limit)
        items = []
        for doc in cursor:
            item = dict(doc)
            item["id"] = str(item["_id"])
            items.append(item)
        return items
    except Exception as error:
        logger.error("Failed to list reviews for mentor %s: %s", mentor_id, error)
        return []


def get_mentor_rating_summary(mentor_id: Any) -> dict[str, Any]:
    try:
        mid = parse_object_id(mentor_id)
        reviews = list(reviews_collection.find({"mentor_id": mid}, {"rating": 1}))
        count = len(reviews)
        if count == 0:
            return {
                "average_rating": 0.0,
                "review_count": 0,
                "has_reviews": False,
                "stars_rounded": 0,
            }
        total = sum(item.get("rating", 0) for item in reviews)
        avg = round(total / count, 1)
        return {
            "average_rating": avg,
            "review_count": count,
            "has_reviews": True,
            "stars_rounded": round(avg),
        }
    except Exception as error:
        logger.error("Failed to calculate rating summary for mentor %s: %s", mentor_id, error)
        return {
            "average_rating": 0.0,
            "review_count": 0,
            "has_reviews": False,
            "stars_rounded": 0,
        }


def list_student_reviews(student_id: Any) -> list[dict[str, Any]]:
    try:
        sid = parse_object_id(student_id)
        cursor = reviews_collection.find({"student_id": sid}).sort("created_at", -1)
        items = []
        for doc in cursor:
            item = dict(doc)
            item["id"] = str(item["_id"])
            items.append(item)
        return items
    except Exception as error:
        logger.error("Failed to list student reviews for %s: %s", student_id, error)
        return []
