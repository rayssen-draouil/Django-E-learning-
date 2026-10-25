from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bson import ObjectId
from django.conf import settings
from pymongo.errors import PyMongoError

from . import repositories

logger = logging.getLogger(__name__)

PREDEFINED_TOPICS = (
    "Python Programming",
    "Web Development",
    "JavaScript",
    "Django",
    "Database Management",
    "Data Structures and Algorithms",
    "Software Engineering",
    "Career Guidance",
    "Other",
)

TOPIC_CHOICES = tuple((topic, topic) for topic in PREDEFINED_TOPICS)

SESSION_TYPES = ("online", "in_person")
SESSION_TYPE_CHOICES = (
    ("online", "Online"),
    ("in_person", "In-person"),
)
SESSION_TYPE_LABELS = dict(SESSION_TYPE_CHOICES)

MIN_DURATION_MINUTES = 15
DEFAULT_MAX_DURATION_MINUTES = 240
MAX_CUSTOM_TOPIC_LENGTH = 120
MAX_LOCATION_LENGTH = 200


class AvailabilityError(Exception):
    """Base exception for mentor availability operations."""


class AvailabilityNotFoundError(AvailabilityError):
    """Raised when an availability slot does not exist or is inactive."""


class AvailabilityPermissionError(AvailabilityError):
    """Raised when a user is not authorized or does not own the availability slot."""


class AvailabilityValidationError(AvailabilityError):
    """Raised when slot data fails business validation rules."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field
        self.message = message


class AvailabilityOverlapError(AvailabilityValidationError):
    """Raised when a slot overlaps with another active slot for the same mentor."""

    def __init__(
        self,
        message: str = "This time overlaps with another availability slot. Try a different time.",
    ):
        super().__init__(message, field=None)


class AvailabilityBookedError(AvailabilityError):
    """Raised when attempting to delete a slot that has an active confirmed booking."""


class AvailabilityStorageError(AvailabilityError):
    """Raised when MongoDB fails to execute an operation."""


def parse_object_id(value: Any) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise AvailabilityNotFoundError("Invalid availability identifier.")
    return ObjectId(value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _app_timezone() -> ZoneInfo:
    tz_name = getattr(settings, "TIME_ZONE", "UTC") or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def current_local_datetime() -> datetime:
    return datetime.now(_app_timezone())


def current_local_date() -> date:
    return current_local_datetime().date()


def get_max_duration_minutes() -> int:
    return int(
        getattr(settings, "MENTOR_MAX_SESSION_MINUTES", DEFAULT_MAX_DURATION_MINUTES)
    )


def _require_mentor_user(user: dict[str, Any] | None) -> ObjectId:
    if not isinstance(user, dict) or user.get("role") != "mentor":
        raise AvailabilityPermissionError(
            "Only authenticated mentors can manage availability."
        )
    raw_id = user.get("_id")
    if raw_id is None:
        raise AvailabilityPermissionError("Invalid mentor session identity.")
    return parse_object_id(raw_id)


def _ensure_owner(slot: dict[str, Any], user: dict[str, Any]) -> ObjectId:
    mentor_id = _require_mentor_user(user)
    if slot.get("mentor_id") != mentor_id:
        raise AvailabilityPermissionError(
            "You do not have permission to access or modify another mentor's availability."
        )
    return mentor_id


def parse_date_value(raw_date: Any) -> date:
    if isinstance(raw_date, datetime):
        return raw_date.date()
    if isinstance(raw_date, date):
        return raw_date
    if isinstance(raw_date, str):
        cleaned = raw_date.strip()
        if not cleaned:
            raise AvailabilityValidationError(
                "Choose a date so students know when you're available.",
                field="date",
            )
        try:
            return date.fromisoformat(cleaned)
        except ValueError as error:
            raise AvailabilityValidationError(
                "Please enter a valid date in YYYY-MM-DD format.",
                field="date",
            ) from error
    raise AvailabilityValidationError(
        "Choose a date so students know when you're available.",
        field="date",
    )


def parse_time_value(raw_time: Any, *, field_name: str, missing_message: str) -> time:
    if isinstance(raw_time, time):
        return raw_time.replace(second=0, microsecond=0)
    if isinstance(raw_time, str):
        cleaned = raw_time.strip()
        if not cleaned:
            raise AvailabilityValidationError(missing_message, field=field_name)
        parts = cleaned.split(":")
        if len(parts) not in (2, 3):
            raise AvailabilityValidationError(
                "Please enter a valid time in HH:MM format.",
                field=field_name,
            )
        try:
            hour = int(parts[0])
            minute = int(parts[1])
            return time(hour=hour, minute=minute)
        except ValueError as error:
            raise AvailabilityValidationError(
                "Please enter a valid time in HH:MM format.",
                field=field_name,
            ) from error
    raise AvailabilityValidationError(missing_message, field=field_name)


def calculate_duration_minutes(start_value: time | str, end_value: time | str) -> int:
    start_t = (
        start_value
        if isinstance(start_value, time)
        else parse_time_value(
            start_value,
            field_name="start_time",
            missing_message="When does your availability begin?",
        )
    )
    end_t = (
        end_value
        if isinstance(end_value, time)
        else parse_time_value(
            end_value,
            field_name="end_time",
            missing_message="Your end time must be later than your start time.",
        )
    )
    start_minutes = start_t.hour * 60 + start_t.minute
    end_minutes = end_t.hour * 60 + end_t.minute
    return end_minutes - start_minutes


def validate_and_normalize_slot_data(
    data: dict[str, Any],
    *,
    reference_date: date | None = None,
    reference_datetime: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise AvailabilityValidationError("Invalid availability payload.")

    now_local = (
        reference_datetime
        if reference_datetime is not None
        else current_local_datetime()
    )
    slot_date = parse_date_value(data.get("date"))
    today = reference_date if reference_date is not None else now_local.date()
    if slot_date < today:
        raise AvailabilityValidationError(
            "That date has already passed. Please select today or a future date.",
            field="date",
        )

    start_t = parse_time_value(
        data.get("start_time"),
        field_name="start_time",
        missing_message="When does your availability begin?",
    )
    end_t = parse_time_value(
        data.get("end_time"),
        field_name="end_time",
        missing_message="Your end time must be later than your start time.",
    )

    if slot_date == today and (reference_date is None or reference_datetime is not None):
        current_hm = now_local.time().replace(second=0, microsecond=0)
        if start_t.replace(second=0, microsecond=0) <= current_hm:
            raise AvailabilityValidationError(
                "This start time has already passed for today. Please choose a future start time.",
                field="start_time",
            )

    duration_minutes = calculate_duration_minutes(start_t, end_t)
    if duration_minutes <= 0:
        raise AvailabilityValidationError(
            "Your end time must be later than your start time.",
            field="end_time",
        )
    if duration_minutes < MIN_DURATION_MINUTES:
        raise AvailabilityValidationError(
            f"Choose a session lasting at least {MIN_DURATION_MINUTES} minutes.",
            field="end_time",
        )

    max_duration = get_max_duration_minutes()
    if duration_minutes > max_duration:
        max_hours = max_duration // 60
        raise AvailabilityValidationError(
            f"Sessions cannot exceed {max_hours} hours ({max_duration} minutes).",
            field="end_time",
        )

    topic = (data.get("topic") or "").strip() if isinstance(data.get("topic"), str) else ""
    if not topic:
        raise AvailabilityValidationError(
            "Choose a mentoring topic to help students find the right session.",
            field="topic",
        )
    if topic not in PREDEFINED_TOPICS:
        raise AvailabilityValidationError(
            "Choose a mentoring topic to help students find the right session.",
            field="topic",
        )

    raw_custom_topic = data.get("custom_topic")
    custom_topic = raw_custom_topic.strip() if isinstance(raw_custom_topic, str) else ""
    if topic == "Other":
        if not custom_topic:
            raise AvailabilityValidationError(
                "Tell us which topic you want to mentor.",
                field="custom_topic",
            )
        if len(custom_topic) > MAX_CUSTOM_TOPIC_LENGTH:
            raise AvailabilityValidationError(
                f"Custom topic must be {MAX_CUSTOM_TOPIC_LENGTH} characters or fewer.",
                field="custom_topic",
            )
    else:
        custom_topic = ""

    raw_session_type = data.get("session_type")
    session_type = raw_session_type.strip() if isinstance(raw_session_type, str) else ""
    if session_type not in SESSION_TYPES:
        raise AvailabilityValidationError(
            "Please select whether this session is Online or In-person.",
            field="session_type",
        )

    raw_location = data.get("location")
    location = raw_location.strip() if isinstance(raw_location, str) else ""
    if session_type == "in_person":
        if len(location) > MAX_LOCATION_LENGTH:
            raise AvailabilityValidationError(
                f"Location must be {MAX_LOCATION_LENGTH} characters or fewer.",
                field="location",
            )
    else:
        location = ""

    return {
        "date": slot_date.isoformat(),
        "start_time": start_t.strftime("%H:%M"),
        "end_time": end_t.strftime("%H:%M"),
        "duration_minutes": duration_minutes,
        "topic": topic,
        "custom_topic": custom_topic,
        "session_type": session_type,
        "location": location,
    }


def ensure_no_overlap(
    mentor_id: ObjectId,
    *,
    date_str: str,
    start_time_str: str,
    end_time_str: str,
    exclude_slot_id: ObjectId | None = None,
) -> None:
    try:
        conflicts = repositories.find_overlapping_slots(
            mentor_id,
            date=date_str,
            start_time=start_time_str,
            end_time=end_time_str,
            exclude_slot_id=exclude_slot_id,
        )
    except PyMongoError as error:
        logger.error("Failed to query overlapping availability slots for mentor %s", mentor_id)
        raise AvailabilityStorageError(
            "We couldn't save your changes. Please check your connection and try again."
        ) from error

    if conflicts:
        raise AvailabilityOverlapError()


def format_slot_for_display(document: dict[str, Any]) -> dict[str, Any]:
    slot = dict(document)
    slot["id"] = str(document["_id"])
    slot["mentor_id_str"] = str(document.get("mentor_id", ""))
    topic = document.get("topic", "")
    custom_topic = document.get("custom_topic", "")
    slot["display_topic"] = custom_topic if topic == "Other" and custom_topic else topic
    slot["session_type_label"] = SESSION_TYPE_LABELS.get(
        document.get("session_type", "online"), "Online"
    )
    status = document.get("status", "available")
    is_booked = bool(document.get("is_booked") or status == "booked" or document.get("booking_id"))
    slot["is_booked"] = is_booked
    slot["status_label"] = "Booked" if is_booked else "Available"
    duration = int(document.get("duration_minutes", 0))
    slot["duration_badge"] = f"{duration} min"
    try:
        slot["date_obj"] = date.fromisoformat(str(document.get("date", "")))
    except ValueError:
        slot["date_obj"] = None

    now_local = current_local_datetime()
    today_iso = now_local.date().isoformat()
    now_time_str = now_local.strftime("%H:%M")
    slot_date_str = str(document.get("date", ""))
    slot_end_str = str(document.get("end_time", ""))
    slot["is_upcoming"] = (
        status == "available"
        and not is_booked
        and (
            slot_date_str > today_iso
            or (slot_date_str == today_iso and slot_end_str >= now_time_str)
        )
    )
    return slot


def list_mentor_slots(
    user: dict[str, Any],
    *,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    mentor_id = _require_mentor_user(user)
    filters = filters or {}
    date_filter = (filters.get("date") or "").strip() or None
    topic_filter = (filters.get("topic") or "").strip() or None
    session_type_filter = (filters.get("session_type") or "").strip() or None

    if topic_filter and topic_filter not in PREDEFINED_TOPICS:
        topic_filter = None
    if session_type_filter and session_type_filter not in SESSION_TYPES:
        session_type_filter = None
    if date_filter:
        try:
            date_filter = date.fromisoformat(date_filter).isoformat()
        except ValueError:
            date_filter = None

    try:
        documents = repositories.list_mentor_availability(
            mentor_id,
            date=date_filter,
            topic=topic_filter,
            session_type=session_type_filter,
        )
    except PyMongoError as error:
        logger.error("Failed to list availability slots for mentor %s", mentor_id)
        raise AvailabilityStorageError(
            "Availability data is temporarily unavailable."
        ) from error

    return [format_slot_for_display(doc) for doc in documents]


def get_mentor_availability_stats(user: dict[str, Any]) -> dict[str, int]:
    mentor_id = _require_mentor_user(user)
    try:
        documents = repositories.list_mentor_availability(mentor_id)
    except PyMongoError as error:
        logger.error("Failed to calculate availability stats for mentor %s", mentor_id)
        raise AvailabilityStorageError(
            "Availability statistics are temporarily unavailable."
        ) from error

    formatted = [format_slot_for_display(doc) for doc in documents]
    total_slots = len(formatted)
    upcoming_slots = sum(1 for item in formatted if item["is_upcoming"])
    online_slots = sum(1 for item in formatted if item.get("session_type") == "online")
    in_person_slots = sum(1 for item in formatted if item.get("session_type") == "in_person")
    booked_slots = sum(1 for item in formatted if item["is_booked"])

    return {
        "total_slots": total_slots,
        "upcoming_slots": upcoming_slots,
        "online_slots": online_slots,
        "in_person_slots": in_person_slots,
        "booked_slots": booked_slots,
    }


def get_managed_slot(slot_id: Any, user: dict[str, Any]) -> dict[str, Any]:
    mentor_id = _require_mentor_user(user)
    object_id = parse_object_id(slot_id)
    try:
        slot = repositories.get_availability_slot(object_id)
    except PyMongoError as error:
        logger.error("Failed to fetch availability slot %s", object_id)
        raise AvailabilityStorageError(
            "Availability storage is temporarily unavailable."
        ) from error

    if not slot:
        raise AvailabilityNotFoundError("Availability slot not found.")
    if slot.get("mentor_id") != mentor_id:
        raise AvailabilityPermissionError(
            "You do not have permission to access another mentor's availability."
        )
    return slot


def create_availability_slot(
    *,
    data: dict[str, Any],
    user: dict[str, Any],
    reference_date: date | None = None,
    reference_datetime: datetime | None = None,
) -> ObjectId:
    mentor_id = _require_mentor_user(user)
    normalized = validate_and_normalize_slot_data(
        data,
        reference_date=reference_date,
        reference_datetime=reference_datetime,
    )

    ensure_no_overlap(
        mentor_id,
        date_str=normalized["date"],
        start_time_str=normalized["start_time"],
        end_time_str=normalized["end_time"],
    )

    now = _now()
    document: dict[str, Any] = {
        "_id": ObjectId(),
        "mentor_id": mentor_id,
        "date": normalized["date"],
        "start_time": normalized["start_time"],
        "end_time": normalized["end_time"],
        "duration_minutes": normalized["duration_minutes"],
        "topic": normalized["topic"],
        "custom_topic": normalized["custom_topic"],
        "session_type": normalized["session_type"],
        "location": normalized["location"],
        "status": "available",
        "is_booked": False,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }

    try:
        inserted_id = repositories.insert_availability(document)
        # Post-insert conflict verification to guard against concurrent overlapping inserts
        conflicts = repositories.find_overlapping_slots(
            mentor_id,
            date=normalized["date"],
            start_time=normalized["start_time"],
            end_time=normalized["end_time"],
            exclude_slot_id=inserted_id,
        )
        if conflicts:
            repositories.delete_availability(inserted_id, mentor_id)
            raise AvailabilityOverlapError()
        return inserted_id
    except AvailabilityOverlapError:
        raise
    except PyMongoError as error:
        logger.error("Failed to create availability slot for mentor %s", mentor_id)
        raise AvailabilityStorageError(
            "We couldn't save your changes. Please check your connection and try again."
        ) from error


def update_availability_slot(
    slot_id: Any,
    *,
    data: dict[str, Any],
    user: dict[str, Any],
    reference_date: date | None = None,
    reference_datetime: datetime | None = None,
) -> dict[str, Any]:
    existing = get_managed_slot(slot_id, user)
    mentor_id = existing["mentor_id"]
    normalized = validate_and_normalize_slot_data(
        data,
        reference_date=reference_date,
        reference_datetime=reference_datetime,
    )

    ensure_no_overlap(
        mentor_id,
        date_str=normalized["date"],
        start_time_str=normalized["start_time"],
        end_time_str=normalized["end_time"],
        exclude_slot_id=existing["_id"],
    )

    fields = {
        **normalized,
        "updated_at": _now(),
    }
    try:
        if not repositories.update_availability(existing["_id"], mentor_id, fields):
            raise AvailabilityNotFoundError("Availability slot not found.")
    except PyMongoError as error:
        logger.error("Failed to update availability slot %s", existing["_id"])
        raise AvailabilityStorageError(
            "We couldn't save your changes. Please check your connection and try again."
        ) from error

    updated = dict(existing)
    updated.update(fields)
    return updated


def delete_availability_slot(slot_id: Any, *, user: dict[str, Any]) -> None:
    existing = get_managed_slot(slot_id, user)
    mentor_id = existing["mentor_id"]

    if (
        existing.get("status") == "booked"
        or existing.get("is_booked") is True
        or existing.get("booking_id")
    ):
        raise AvailabilityBookedError(
            "This availability slot has a confirmed booking and cannot be deleted until the booking is cancelled or rescheduled."
        )

    try:
        if not repositories.deactivate_availability(existing["_id"], mentor_id, _now()):
            raise AvailabilityNotFoundError("Availability slot not found.")
    except PyMongoError as error:
        logger.error("Failed to delete availability slot %s", existing["_id"])
        raise AvailabilityStorageError(
            "We couldn't remove this availability slot right now. Please try again."
        ) from error
