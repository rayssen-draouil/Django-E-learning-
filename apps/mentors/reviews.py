from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError

from config.mongodb import db
from . import email_service, notifications

logger = logging.getLogger(__name__)

reviews_collection: Collection[dict[str, Any]] = db["mentor_reviews"]
bookings_collection: Collection[dict[str, Any]] = db["mentorship_bookings"]
users_collection: Collection[dict[str, Any]] = db["users"]

REVIEW_EDIT_WINDOW_DAYS = 14
BAYESIAN_PRIOR_COUNT = 3.0
BAYESIAN_PRIOR_MEAN = 4.2

LEARNING_OUTCOME_CHOICES = (
    ("achieved_goal", "Achieved my learning goal"),
    ("made_progress", "Made solid progress"),
    ("needs_more_help", "Still need follow-up guidance"),
)
LEARNING_OUTCOME_LABELS = dict(LEARNING_OUTCOME_CHOICES)

STRENGTH_TAG_CHOICES = (
    ("clear_explanations", "Clear Explanations"),
    ("practical_code", "Actionable Code Examples"),
    ("patient_supportive", "Patient & Encouraging"),
    ("deep_expertise", "Deep Technical Mastery"),
    ("great_debugging", "Great Debugging Partner"),
    ("well_prepared", "Well-Prepared & Structured"),
    ("actionable_advice", "Actionable Career Advice"),
)
STRENGTH_TAG_LABELS = dict(STRENGTH_TAG_CHOICES)
ALLOWED_STRENGTH_TAGS = tuple(STRENGTH_TAG_LABELS.keys()) + tuple(STRENGTH_TAG_LABELS.values())

REPORT_REASONS = (
    ("spam", "Spam or promotional content"),
    ("offensive", "Offensive or disrespectful language"),
    ("privacy", "Exposes private or personal information"),
    ("inaccurate", "Unrelated to the mentoring session"),
)
VALID_REPORT_REASONS = {key for key, _ in REPORT_REASONS}

# Content safety patterns
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_PATTERN = re.compile(r"(?:\+?\d[\d\-\s()]{7,}\d)")
_PROHIBITED_WORDS = {
    "scammer",
    "idiot",
    "stupid",
    "moron",
    "hate speech",
    "kill yourself",
    "fuck",
    "shit",
    "bitch",
    "asshole",
}


class ReviewError(Exception):
    """Base error for review operations."""


class ReviewValidationError(ReviewError):
    """Raised when review data fails validation."""


class ReviewDuplicateError(ReviewValidationError):
    """Raised when a review already exists for this booking."""


class ReviewPermissionError(ReviewValidationError):
    """Raised when user is unauthorized to submit or modify this review."""


class ReviewNotFoundError(ReviewError):
    """Raised when a target review cannot be found."""


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
            [("mentor_id", 1), ("status", 1), ("created_at", -1)],
            name="mentor_reviews_mentor_status_recent",
        )
        reviews_collection.create_index(
            [("student_id", 1), ("created_at", -1)],
            name="mentor_reviews_student_recent",
        )
    except PyMongoError:
        logger.warning("Could not create mentor_reviews indexes.")


def _validate_star_value(value: Any, field_label: str = "Rating") -> int:
    try:
        rating_int = int(value)
        if rating_int < 1 or rating_int > 5:
            raise ValueError
        return rating_int
    except (TypeError, ValueError):
        raise ReviewValidationError(f"{field_label} must be an integer between 1 and 5 stars.")


def _validate_safe_text(text: str, *, field_label: str = "Review text", max_length: int = 1000) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) > max_length:
        raise ReviewValidationError(f"{field_label} must be {max_length} characters or fewer.")

    lowered = cleaned.lower()
    for bad_word in _PROHIBITED_WORDS:
        if re.search(rf"\b{re.escape(bad_word)}\b", lowered):
            raise ReviewValidationError(
                f"{field_label} contains inappropriate or abusive language. Please keep feedback constructive."
            )

    if _EMAIL_PATTERN.search(cleaned) or _PHONE_PATTERN.search(cleaned):
        raise ReviewValidationError(
            f"For privacy protection, please do not include personal email addresses or phone numbers in {field_label.lower()}."
        )

    return cleaned


def _clean_strength_tags(tags: Any) -> list[str]:
    if not tags:
        return []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not isinstance(tags, (list, tuple, set)):
        return []
    allowed_set = set(ALLOWED_STRENGTH_TAGS)
    seen: list[str] = []
    for tag in tags:
        clean_tag = str(tag).strip()
        if clean_tag in allowed_set and clean_tag not in seen:
            seen.append(clean_tag)
    return seen[:6]


def _format_review_for_display(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    rating = int(doc.get("rating", 5))
    helpfulness = int(doc.get("helpfulness_rating", rating))
    clarity = int(doc.get("clarity_rating", rating))
    communication = int(doc.get("communication_rating", rating))
    outcome = doc.get("learning_outcome") or ("achieved_goal" if rating >= 4 else "made_progress")

    created_at = doc.get("created_at")
    can_edit = False
    if isinstance(created_at, datetime):
        created_utc = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        can_edit = (_now() - created_utc) <= timedelta(days=REVIEW_EDIT_WINDOW_DAYS) and doc.get("status", "published") == "published"

    # Never expose private booking fields (learning_objective, problem_description, meeting_link, student_email)
    return {
        "_id": doc["_id"],
        "id": str(doc["_id"]),
        "booking_id": doc.get("booking_id"),
        "booking_id_str": str(doc.get("booking_id", "")),
        "student_id": doc.get("student_id"),
        "student_name": doc.get("student_name", "Verified Student"),
        "mentor_id": doc.get("mentor_id"),
        "mentor_name": doc.get("mentor_name", ""),
        "topic": doc.get("topic", "Mentorship Session"),
        "session_date": doc.get("session_date", ""),
        "rating": rating,
        "helpfulness_rating": helpfulness,
        "clarity_rating": clarity,
        "communication_rating": communication,
        "learning_outcome": outcome,
        "learning_outcome_label": LEARNING_OUTCOME_LABELS.get(outcome, "Made solid progress"),
        "review_text": doc.get("review_text", ""),
        "strength_tags": doc.get("strength_tags", []),
        "matched_goal": bool(doc.get("matched_goal", True)),
        "verified_session": True,
        "status": doc.get("status", "published"),
        "is_flagged": bool(doc.get("is_flagged", False)),
        "is_edited": bool(doc.get("is_edited", False)),
        "can_edit": can_edit,
        "mentor_response": doc.get("mentor_response", ""),
        "mentor_responded_at": doc.get("mentor_responded_at"),
        "created_at": created_at,
        "updated_at": doc.get("updated_at", created_at),
    }


def create_review(
    *,
    booking: dict[str, Any],
    student_user: dict[str, Any],
    rating: int | str,
    review_text: str = "",
    helpfulness_rating: int | str | None = None,
    clarity_rating: int | str | None = None,
    communication_rating: int | str | None = None,
    learning_outcome: str | None = None,
    strength_tags: list[str] | None = None,
    matched_goal: bool = True,
    allow_empty_text: bool = False,
) -> ObjectId:
    if not isinstance(student_user, dict):
        raise ReviewPermissionError("Authentication required.")
    student_id = parse_object_id(student_user.get("_id"))

    if not isinstance(booking, dict):
        raise ReviewValidationError("Valid booking record is required.")

    booking_id = parse_object_id(booking.get("_id"))

    # Cross-verify against persisted booking record if status/student_id not already present in booking dict
    if "status" in booking and "student_id" in booking and "mentor_id" in booking:
        effective_booking = booking
    else:
        try:
            persisted_booking = bookings_collection.find_one({"_id": booking_id})
        except Exception:
            persisted_booking = None
        effective_booking = persisted_booking if isinstance(persisted_booking, dict) else booking

    # Verify student owns the booking
    booking_student_id = parse_object_id(effective_booking.get("student_id"))
    if student_id != booking_student_id:
        raise ReviewPermissionError("You can only review sessions you participated in as a student.")

    # Verify mentor is not rating themselves
    mentor_id = parse_object_id(effective_booking.get("mentor_id"))
    if student_id == mentor_id:
        raise ReviewPermissionError("Mentors cannot review their own coaching sessions.")

    # Verify booking status is completed and attendance was not a no-show
    if effective_booking.get("status") != "completed":
        raise ReviewValidationError(
            "Reviews can only be submitted for completed mentoring sessions."
        )
    if effective_booking.get("attendance_status") == "no_show":
        raise ReviewValidationError(
            "Reviews cannot be submitted for sessions marked as a no-show."
        )

    # Validate overall rating and sub-ratings
    rating_int = _validate_star_value(rating, "Overall rating")
    helpfulness_int = (
        _validate_star_value(helpfulness_rating, "Helpfulness rating")
        if helpfulness_rating not in (None, "")
        else rating_int
    )
    clarity_int = (
        _validate_star_value(clarity_rating, "Clarity rating")
        if clarity_rating not in (None, "")
        else rating_int
    )
    communication_int = (
        _validate_star_value(communication_rating, "Communication rating")
        if communication_rating not in (None, "")
        else rating_int
    )

    # Validate learning outcome
    outcome_val = (learning_outcome or "").strip()
    if not outcome_val:
        outcome_val = "achieved_goal" if rating_int >= 4 else "made_progress"
    elif outcome_val not in LEARNING_OUTCOME_LABELS:
        raise ReviewValidationError("Please select a valid learning outcome.")

    # Validate review text
    raw_text = (review_text or "").strip()
    if not raw_text and not allow_empty_text:
        raise ReviewValidationError("Please provide at least 10 characters of constructive feedback.")
    if raw_text and len(raw_text) < 10:
        raise ReviewValidationError("Please provide at least 10 characters of constructive feedback.")
    cleaned_text = _validate_safe_text(raw_text, field_label="Review text", max_length=1000)

    cleaned_tags = _clean_strength_tags(strength_tags)

    student_name = (
        f"{student_user.get('first_name', '')} {student_user.get('last_name', '')}".strip()
        or "Student"
    )
    mentor_name = str(effective_booking.get("mentor_name", "")).strip()

    now = _now()
    doc: dict[str, Any] = {
        "_id": ObjectId(),
        "booking_id": booking_id,
        "student_id": student_id,
        "student_name": student_name,
        "mentor_id": mentor_id,
        "mentor_name": mentor_name,
        "topic": str(effective_booking.get("topic", "Mentoring")).strip(),
        "session_date": str(effective_booking.get("date", "")).strip(),
        "rating": rating_int,
        "helpfulness_rating": helpfulness_int,
        "clarity_rating": clarity_int,
        "communication_rating": communication_int,
        "learning_outcome": outcome_val,
        "review_text": cleaned_text,
        "strength_tags": cleaned_tags,
        "matched_goal": bool(matched_goal),
        "verified_session": True,
        "status": "published",
        "is_flagged": False,
        "flag_reports": [],
        "is_edited": False,
        "edit_history": [],
        "mentor_response": "",
        "mentor_responded_at": None,
        "created_at": now,
        "updated_at": now,
    }

    try:
        reviews_collection.insert_one(doc)
    except DuplicateKeyError as error:
        raise ReviewDuplicateError("A review has already been submitted for this session.") from error
    except PyMongoError as error:
        logger.error("Failed to insert review for booking %s: %s", booking_id, error)
        raise ReviewError("Could not save review. Please try again.") from error

    # Notify mentor in-app and via email (non-blocking) when multi-dimensional or view-triggered
    if allow_empty_text or helpfulness_rating is not None or learning_outcome is not None:
        try:
            notifications.create_notification(
                recipient_id=mentor_id,
                event_type="review_received",
                title=f"New {rating_int}-Star Review Received",
                message=f"{student_name} shared verified feedback on your {doc['topic']} session.",
                link_url="/users/backoffice/mentors/sessions/",
                related_booking_id=booking_id,
            )
            mentor_email = effective_booking.get("mentor_email")
            if mentor_email:
                email_service.send_templated_email(
                    subject=f"New {rating_int}-Star Review from {student_name} — SkillForge",
                    recipient_email=mentor_email,
                    template_name_base="new_review_mentor",
                    context={
                        "mentor_name": mentor_name or "Mentor",
                        "student_name": student_name,
                        "rating": rating_int,
                        "helpfulness_rating": helpfulness_int,
                        "clarity_rating": clarity_int,
                        "communication_rating": communication_int,
                        "learning_outcome_label": LEARNING_OUTCOME_LABELS.get(outcome_val, ""),
                        "review_text": cleaned_text or "(Star rating submitted without written comment)",
                        "topic": doc["topic"],
                        "action_url": "/users/backoffice/mentors/sessions/",
                    },
                )
        except Exception as notify_err:
            logger.warning("Non-fatal error sending review notification: %s", notify_err)

    return doc["_id"]


def update_review(
    *,
    booking_id: Any = None,
    review_id: Any = None,
    student_user: dict[str, Any],
    rating: int | str,
    review_text: str = "",
    helpfulness_rating: int | str | None = None,
    clarity_rating: int | str | None = None,
    communication_rating: int | str | None = None,
    learning_outcome: str | None = None,
    strength_tags: list[str] | None = None,
    matched_goal: bool = True,
    allow_empty_text: bool = True,
) -> dict[str, Any]:
    if not isinstance(student_user, dict):
        raise ReviewPermissionError("Authentication required.")
    student_id = parse_object_id(student_user.get("_id"))

    if review_id is not None:
        rid = parse_object_id(review_id)
        existing = reviews_collection.find_one({"_id": rid})
    else:
        bid = parse_object_id(booking_id)
        existing = reviews_collection.find_one({"booking_id": bid})
    if not existing:
        raise ReviewNotFoundError("Review not found for this session.")

    if parse_object_id(existing.get("student_id")) != student_id:
        raise ReviewPermissionError("You can only edit your own reviews.")

    if existing.get("status") == "hidden":
        raise ReviewPermissionError("This review has been moderated and cannot be edited.")

    created_at = existing.get("created_at")
    now = _now()
    if isinstance(created_at, datetime):
        created_utc = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        if (now - created_utc) > timedelta(days=REVIEW_EDIT_WINDOW_DAYS):
            raise ReviewValidationError(
                f"Reviews can only be edited within {REVIEW_EDIT_WINDOW_DAYS} days of submission."
            )

    rating_int = _validate_star_value(rating, "Overall rating")
    helpfulness_int = (
        _validate_star_value(helpfulness_rating, "Helpfulness rating")
        if helpfulness_rating not in (None, "")
        else rating_int
    )
    clarity_int = (
        _validate_star_value(clarity_rating, "Clarity rating")
        if clarity_rating not in (None, "")
        else rating_int
    )
    communication_int = (
        _validate_star_value(communication_rating, "Communication rating")
        if communication_rating not in (None, "")
        else rating_int
    )

    outcome_val = (learning_outcome or "").strip()
    if not outcome_val:
        outcome_val = existing.get("learning_outcome", "achieved_goal")
    elif outcome_val not in LEARNING_OUTCOME_LABELS:
        raise ReviewValidationError("Please select a valid learning outcome.")

    raw_text = (review_text or "").strip()
    if not raw_text and not allow_empty_text:
        raise ReviewValidationError("Please provide at least 10 characters of constructive feedback.")
    if raw_text and len(raw_text) < 10:
        raise ReviewValidationError("Please provide at least 10 characters of constructive feedback.")
    cleaned_text = _validate_safe_text(raw_text, field_label="Review text", max_length=1000)
    cleaned_tags = _clean_strength_tags(strength_tags)

    history_entry = {
        "previous_rating": existing.get("rating"),
        "previous_helpfulness_rating": existing.get("helpfulness_rating"),
        "previous_clarity_rating": existing.get("clarity_rating"),
        "previous_communication_rating": existing.get("communication_rating"),
        "previous_learning_outcome": existing.get("learning_outcome"),
        "previous_review_text": existing.get("review_text", ""),
        "edited_at": now,
    }

    prev_history = list(existing.get("edit_history") or [])
    prev_history.append(history_entry)

    update_fields = {
        "rating": rating_int,
        "helpfulness_rating": helpfulness_int,
        "clarity_rating": clarity_int,
        "communication_rating": communication_int,
        "learning_outcome": outcome_val,
        "review_text": cleaned_text,
        "strength_tags": cleaned_tags,
        "matched_goal": bool(matched_goal),
        "is_edited": True,
        "edit_history": prev_history,
        "updated_at": now,
    }

    try:
        reviews_collection.update_one(
            {"_id": existing["_id"], "student_id": student_id},
            {"$set": update_fields},
        )
    except PyMongoError as error:
        logger.error("Failed to update review %s: %s", existing["_id"], error)
        raise ReviewError("Could not update review. Please try again.") from error

    updated_doc = {**existing, **update_fields}
    return _format_review_for_display(updated_doc) or updated_doc


def add_mentor_response(
    *,
    review_id: Any,
    mentor_user: dict[str, Any],
    response_text: str,
) -> bool:
    if not isinstance(mentor_user, dict) or mentor_user.get("role") != "mentor":
        raise ReviewPermissionError("Only mentors can respond to session reviews.")
    mid = parse_object_id(mentor_user.get("_id"))
    rid = parse_object_id(review_id)

    review = reviews_collection.find_one({"_id": rid})
    if not review:
        raise ReviewNotFoundError("Review not found.")

    if parse_object_id(review.get("mentor_id")) != mid:
        raise ReviewPermissionError("You can only respond to reviews for your own sessions.")

    cleaned_response = _validate_safe_text(
        response_text,
        field_label="Mentor response",
        max_length=600,
    )
    if len(cleaned_response) < 5:
        raise ReviewValidationError("Please provide at least 5 characters in your response.")

    now = _now()
    reviews_collection.update_one(
        {"_id": rid, "mentor_id": mid},
        {
            "$set": {
                "mentor_response": cleaned_response,
                "mentor_responded_at": now,
                "updated_at": now,
            }
        },
    )

    try:
        mentor_name = f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Your Mentor"
        notifications.create_notification(
            recipient_id=review.get("student_id"),
            event_type="review_response_added",
            title="Mentor Responded to Your Review",
            message=f"{mentor_name} replied to your feedback on {review.get('topic', 'your session')}.",
            link_url=f"/users/mentors/{mid}/",
            related_booking_id=review.get("booking_id"),
        )
    except Exception:
        pass

    return True


def report_review(
    *,
    review_id: Any,
    reporter_user: dict[str, Any],
    reason: str,
    details: str = "",
) -> bool:
    if not isinstance(reporter_user, dict):
        raise ReviewPermissionError("Authentication required to report a review.")
    uid = parse_object_id(reporter_user.get("_id"))
    rid = parse_object_id(review_id)

    clean_reason = (reason or "").strip()
    if clean_reason.lower() not in VALID_REPORT_REASONS and len(clean_reason) < 5:
        raise ReviewValidationError("Please provide a valid report reason (at least 5 characters).")

    review = reviews_collection.find_one({"_id": rid})
    if not review:
        raise ReviewNotFoundError("Review not found.")

    clean_details = (details or "").strip()[:300]
    report_entry = {
        "reporter_id": uid,
        "reason": clean_reason,
        "details": clean_details,
        "reported_at": _now(),
    }

    reviews_collection.update_one(
        {"_id": rid},
        {
            "$set": {"is_flagged": True},
            "$push": {"flag_reports": report_entry},
        },
    )
    return True


def moderate_review(
    *,
    review_id: Any,
    admin_user: dict[str, Any],
    action: str,
    note: str = "",
) -> bool:
    if not isinstance(admin_user, dict) or admin_user.get("role") != "admin":
        raise ReviewPermissionError("Only administrators can moderate reviews.")
    rid = parse_object_id(review_id)
    clean_action = (action or "").strip().lower()
    if clean_action not in {"hide", "restore"}:
        raise ReviewValidationError("Moderation action must be 'hide' or 'restore'.")

    new_status = "hidden" if clean_action == "hide" else "published"
    result = reviews_collection.update_one(
        {"_id": rid},
        {
            "$set": {
                "status": new_status,
                "is_flagged": False if clean_action == "restore" else True,
                "moderation_note": (note or "").strip()[:300],
                "moderated_by": parse_object_id(admin_user.get("_id")),
                "moderated_at": _now(),
            }
        },
    )
    return result.matched_count == 1


def get_review_by_booking(booking_id: Any) -> dict[str, Any] | None:
    try:
        bid = parse_object_id(booking_id)
        doc = reviews_collection.find_one({"booking_id": bid})
        return _format_review_for_display(doc)
    except Exception:
        return None


def list_mentor_reviews(
    mentor_id: Any,
    limit: int = 50,
    offset: int = 0,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    try:
        mid = parse_object_id(mentor_id)
        query: dict[str, Any] = {"mentor_id": mid}
        if not include_hidden:
            query["status"] = {"$ne": "hidden"}
        cursor = (
            reviews_collection.find(query)
            .sort("created_at", -1)
            .skip(max(0, offset))
            .limit(max(1, limit))
        )
        items = []
        for doc in cursor:
            formatted = _format_review_for_display(doc)
            if formatted:
                items.append(formatted)
        return items
    except Exception as error:
        logger.error("Failed to list reviews for mentor %s: %s", mentor_id, error)
        return []


def get_mentor_rating_summary(mentor_id: Any) -> dict[str, Any]:
    empty_summary = {
        "average_rating": 0.0,
        "raw_average": 0.0,
        "bayesian_rating": 0.0,
        "review_count": 0,
        "has_reviews": False,
        "stars_rounded": 0,
        "confidence_level": "new",
        "confidence_label": "Not rated yet",
        "avg_helpfulness": 0.0,
        "avg_clarity": 0.0,
        "avg_communication": 0.0,
        "distribution": {5: 0, 4: 0, 3: 0, 2: 0, 1: 0},
        "distribution_pct": {5: 0, 4: 0, 3: 0, 2: 0, 1: 0},
        "distribution_rows": [
            {"stars": s, "count": 0, "pct": 0} for s in (5, 4, 3, 2, 1)
        ],
        "outcomes": {"achieved_goal": 0, "made_progress": 0, "needs_more_help": 0},
        "positive_outcome_pct": 0,
        "goal_match_pct": 0,
        "top_tags": [],
    }
    try:
        mid = parse_object_id(mentor_id)
        raw_reviews = list(
            reviews_collection.find(
                {"mentor_id": mid},
                {
                    "rating": 1,
                    "helpfulness_rating": 1,
                    "clarity_rating": 1,
                    "communication_rating": 1,
                    "learning_outcome": 1,
                    "strength_tags": 1,
                    "matched_goal": 1,
                    "status": 1,
                    "is_deleted": 1,
                },
            )
        )
        # Filter out hidden/deleted reviews while supporting minimal mocked documents
        valid_reviews = [
            r
            for r in raw_reviews
            if r.get("status", "published") != "hidden"
            and not r.get("is_deleted", False)
            and isinstance(r.get("rating"), (int, float))
            and 1 <= int(r.get("rating")) <= 5
        ]
        count = len(valid_reviews)
        if count == 0:
            return empty_summary

        total_rating = 0
        total_helpfulness = 0
        total_clarity = 0
        total_comm = 0
        dist = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
        outcomes = {"achieved_goal": 0, "made_progress": 0, "needs_more_help": 0}
        matched_goal_count = 0
        tag_counter: Counter[str] = Counter()

        for item in valid_reviews:
            r_val = int(item.get("rating", 5))
            total_rating += r_val
            dist[r_val] = dist.get(r_val, 0) + 1

            total_helpfulness += int(item.get("helpfulness_rating", r_val))
            total_clarity += int(item.get("clarity_rating", r_val))
            total_comm += int(item.get("communication_rating", r_val))

            out_key = item.get("learning_outcome") or ("achieved_goal" if r_val >= 4 else "made_progress")
            if out_key in outcomes:
                outcomes[out_key] += 1

            if item.get("matched_goal", True):
                matched_goal_count += 1

            for t in item.get("strength_tags", []) or []:
                if isinstance(t, str) and t.strip():
                    tag_counter[t.strip()] += 1

        raw_avg = total_rating / count
        avg = round(raw_avg, 1)
        bayesian = round(
            (BAYESIAN_PRIOR_COUNT * BAYESIAN_PRIOR_MEAN + total_rating)
            / (BAYESIAN_PRIOR_COUNT + count),
            2,
        )

        if count <= 2:
            conf_level = "low"
            conf_label = f"Early feedback ({count} {'review' if count == 1 else 'reviews'})"
        elif count <= 6:
            conf_level = "medium"
            conf_label = f"Established rating ({count} verified reviews)"
        else:
            conf_level = "high"
            conf_label = f"Highly verified ({count} verified reviews)"

        dist_pct = {
            star: round((star_count / count) * 100)
            for star, star_count in dist.items()
        }
        dist_rows = [
            {"stars": s, "count": dist[s], "pct": dist_pct[s]}
            for s in (5, 4, 3, 2, 1)
        ]

        positive_outcomes = outcomes["achieved_goal"] + outcomes["made_progress"]
        positive_pct = round((positive_outcomes / count) * 100)
        goal_match_pct = round((matched_goal_count / count) * 100)

        top_tags = [
            {
                "tag": tag,
                "label": STRENGTH_TAG_LABELS.get(tag, tag),
                "count": t_count,
            }
            for tag, t_count in tag_counter.most_common(4)
        ]

        return {
            "average_rating": avg,
            "raw_average": raw_avg,
            "bayesian_rating": bayesian,
            "review_count": count,
            "has_reviews": True,
            "stars_rounded": round(avg),
            "confidence_level": conf_level,
            "confidence_label": conf_label,
            "avg_helpfulness": round(total_helpfulness / count, 1),
            "avg_clarity": round(total_clarity / count, 1),
            "avg_communication": round(total_comm / count, 1),
            "distribution": dist,
            "distribution_pct": dist_pct,
            "distribution_rows": dist_rows,
            "outcomes": outcomes,
            "positive_outcome_pct": positive_pct,
            "goal_match_pct": goal_match_pct,
            "top_tags": top_tags,
        }
    except Exception as error:
        logger.error("Failed to calculate rating summary for mentor %s: %s", mentor_id, error)
        return empty_summary


def list_student_reviews(student_id: Any) -> list[dict[str, Any]]:
    try:
        sid = parse_object_id(student_id)
        cursor = reviews_collection.find({"student_id": sid}).sort("created_at", -1)
        items = []
        for doc in cursor:
            formatted = _format_review_for_display(doc)
            if formatted:
                items.append(formatted)
        return items
    except Exception as error:
        logger.error("Failed to list student reviews for %s: %s", student_id, error)
        return []
