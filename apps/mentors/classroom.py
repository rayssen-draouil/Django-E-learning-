from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time as time_module
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bson import ObjectId
from django.conf import settings
from livekit import api as lk_api
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError

from config.mongodb import db
from . import email_service, notifications
from .recommendations import get_groq_config, sanitize_student_goal

logger = logging.getLogger(__name__)

bookings_collection: Collection[dict[str, Any]] = db["mentorship_bookings"]
users_collection: Collection[dict[str, Any]] = db["users"]
classroom_events_collection: Collection[dict[str, Any]] = db["classroom_events"]
classroom_messages_collection: Collection[dict[str, Any]] = db["classroom_messages"]
classroom_chat_collection: Collection[dict[str, Any]] = classroom_messages_collection

# ---------------------------------------------------------------------------
# Classroom Lifecycle & Invitation Status Constants
# ---------------------------------------------------------------------------

CLASSROOM_STATUS_REQUESTED = "requested"
CLASSROOM_STATUS_SCHEDULED = "scheduled"
CLASSROOM_STATUS_READY = "ready_to_join"
CLASSROOM_STATUS_IN_PROGRESS = "in_progress"
CLASSROOM_STATUS_COMPLETED = "completed"
CLASSROOM_STATUS_CANCELLED = "cancelled"
CLASSROOM_STATUS_REJECTED = "rejected"
CLASSROOM_STATUS_RESCHEDULE_PENDING = "reschedule_pending"
CLASSROOM_STATUS_NO_SHOW = "no_show"
CLASSROOM_STATUS_EXPIRED = "expired"
CLASSROOM_STATUS_NOT_APPLICABLE = "not_applicable"

INVITATION_STATUS_NOT_REQUIRED = "not_required"
INVITATION_STATUS_PENDING = "pending"
INVITATION_STATUS_SENT = "sent"
INVITATION_STATUS_FAILED = "failed"
INVITATION_STATUS_SUPERSEDED = "superseded"

DEFAULT_TOKEN_TTL_SECONDS = 900  # 15 minutes
DEFAULT_EARLY_JOIN_MINUTES = 15
DEFAULT_LATE_GRACE_MINUTES = 180
MAX_CHAT_MESSAGE_LENGTH = 500
MAX_AI_PROMPT_LENGTH = 600

# Simple in-memory rate-limit sliding window per (user_id, action_key)
_RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}

_PLACEHOLDER_PATTERNS = (
    "your-project-livekit-url",
    "your_livekit_api_key",
    "your_livekit_api_secret",
    "replace-with",
    "changeme",
    "example.com",
    "placeholder",
    "<your",
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ClassroomError(Exception):
    """Base exception for online classroom operations."""


class ClassroomNotFoundError(ClassroomError):
    """Raised when a booking or classroom session does not exist."""


class ClassroomConfigurationError(ClassroomError):
    """Raised when LiveKit settings are missing or contain placeholders."""


ClassroomConfigError = ClassroomConfigurationError


class ClassroomPermissionError(ClassroomError):
    """Raised when a user is not authorized to access a classroom or action."""


class ClassroomStateError(ClassroomError):
    """Raised when a booking's status, type, or schedule forbids joining."""

    def __init__(self, message: str, *, state_code: str = "invalid_state", extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.state_code = state_code
        self.reason_code = state_code
        self.extra = extra or {}


ClassroomScheduleError = ClassroomStateError


class ClassroomRateLimitError(ClassroomError):
    """Raised when token or retry requests exceed the permitted rate."""


class ClassroomWebhookSignatureError(ClassroomPermissionError):
    """Raised when a LiveKit webhook signature cannot be verified."""


# ---------------------------------------------------------------------------
# Utility & Index Initialization
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _app_timezone() -> ZoneInfo:
    tz_name = getattr(settings, "TIME_ZONE", "UTC") or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def get_timezone_label() -> str:
    return getattr(settings, "TIME_ZONE", "UTC") or "UTC"


def parse_object_id(value: Any) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    raise ClassroomPermissionError("Classroom session not found or access denied.")


def create_indexes() -> None:
    try:
        classroom_events_collection.create_index(
            [("booking_id", 1), ("created_at", -1)],
            name="classroom_events_booking_recent",
        )
        classroom_events_collection.create_index(
            [("external_event_id", 1)],
            unique=True,
            sparse=True,
            name="classroom_events_external_id_unique",
        )
        classroom_messages_collection.create_index(
            [("booking_id", 1), ("created_at", 1)],
            name="classroom_messages_booking_chronological",
        )
        bookings_collection.create_index(
            [("livekit_room_name", 1)],
            sparse=True,
            name="bookings_livekit_room_name",
        )
    except PyMongoError:
        logger.warning("Could not create online classroom MongoDB indexes.")


def check_rate_limit(key: str, *, limit: int = 15, window_seconds: int = 60) -> None:
    now_ts = time_module.monotonic()
    bucket = _RATE_LIMIT_BUCKETS.get(key, [])
    bucket = [t for t in bucket if (now_ts - t) < window_seconds]
    if len(bucket) >= limit:
        raise ClassroomRateLimitError(
            "Too many requests in a short period. Please wait a moment and try again."
        )
    bucket.append(now_ts)
    _RATE_LIMIT_BUCKETS[key] = bucket


def reset_rate_limits() -> None:
    _RATE_LIMIT_BUCKETS.clear()


# ---------------------------------------------------------------------------
# LiveKit Configuration Inspection & Validation
# ---------------------------------------------------------------------------

def _is_placeholder(val: str) -> bool:
    cleaned = (val or "").strip().lower()
    if not cleaned:
        return True
    return any(pat in cleaned for pat in _PLACEHOLDER_PATTERNS)


def get_livekit_config() -> dict[str, Any]:
    """
    Read LiveKit configuration from Django settings / environment variables.
    Never logs or exposes secret values to callers outside server token signing.
    """
    url = (
        getattr(settings, "LIVEKIT_URL", None)
        or os.environ.get("LIVEKIT_URL", "")
    ).strip()
    api_key = (
        getattr(settings, "LIVEKIT_API_KEY", None)
        or os.environ.get("LIVEKIT_API_KEY", "")
    ).strip()
    api_secret = (
        getattr(settings, "LIVEKIT_API_SECRET", None)
        or os.environ.get("LIVEKIT_API_SECRET", "")
    ).strip()
    ttl = int(
        getattr(settings, "LIVEKIT_TOKEN_TTL_SECONDS", DEFAULT_TOKEN_TTL_SECONDS)
        or DEFAULT_TOKEN_TTL_SECONDS
    )

    missing_or_invalid: list[str] = []
    if _is_placeholder(url) or not (url.startswith("wss://") or url.startswith("ws://")):
        missing_or_invalid.append("LIVEKIT_URL (must be a valid wss:// or ws:// URL)")
    if _is_placeholder(api_key):
        missing_or_invalid.append("LIVEKIT_API_KEY")
    if _is_placeholder(api_secret):
        missing_or_invalid.append("LIVEKIT_API_SECRET")

    return {
        "url": url,
        "api_key": api_key,
        "api_secret": api_secret,
        "ttl_seconds": max(60, min(ttl, 3600)),
        "is_configured": len(missing_or_invalid) == 0,
        "missing_settings": missing_or_invalid,
    }


def validate_livekit_config() -> dict[str, Any]:
    """
    Validate that LiveKit settings are present and not placeholders.
    Raises ClassroomConfigurationError with safe diagnostic guidance if invalid.
    """
    cfg = get_livekit_config()
    if not cfg["is_configured"]:
        settings_list = ", ".join(cfg["missing_settings"])
        raise ClassroomConfigurationError(
            f"LiveKit virtual classroom is not configured properly. "
            f"Please check the following environment settings in .env: {settings_list}."
        )
    return cfg


# ---------------------------------------------------------------------------
# Stable Room Identity & URL Construction
# ---------------------------------------------------------------------------

def build_classroom_path(booking_id: Any) -> str:
    return f"/users/mentorship/classroom/{booking_id}/"


def build_absolute_classroom_url(booking_id: Any) -> str:
    base_url = (getattr(settings, "SITE_URL", "https://django-e-learning.onrender.com") or "https://django-e-learning.onrender.com").rstrip("/")
    return f"{base_url}{build_classroom_path(booking_id)}"


build_classroom_url = build_absolute_classroom_url


def generate_stable_room_name(booking_id: ObjectId) -> str:
    """
    Generate a deterministic, opaque, non-PII room identifier tied to the booking ID.
    Never includes email addresses, names, or personal information.
    """
    secret_salt = (getattr(settings, "SECRET_KEY", "skillforge") or "skillforge").encode("utf-8")
    digest = hmac.new(secret_salt, str(booking_id).encode("utf-8"), hashlib.sha256).hexdigest()[:12]
    return f"sf-room-{booking_id}-{digest}"


def ensure_booking_room_name(booking: dict[str, Any]) -> str:
    """
    Ensure an online booking has a single stable LiveKit room identifier persisted in MongoDB.
    Idempotent across concurrent or repeated calls.
    """
    existing_room = (booking.get("livekit_room_name") or "").strip()
    if existing_room:
        return existing_room

    bid = parse_object_id(booking["_id"])
    candidate_room = generate_stable_room_name(bid)

    bookings_collection.update_one(
        {
            "_id": bid,
            "$or": [
                {"livekit_room_name": {"$exists": False}},
                {"livekit_room_name": ""},
                {"livekit_room_name": None},
            ],
        },
        {
            "$set": {
                "livekit_room_name": candidate_room,
                "updated_at": _now(),
            }
        },
    )
    refreshed = bookings_collection.find_one({"_id": bid}, {"livekit_room_name": 1})
    return (refreshed or {}).get("livekit_room_name") or candidate_room


# ---------------------------------------------------------------------------
# Schedule Window & Authorization Policy
# ---------------------------------------------------------------------------

def get_booking_schedule_bounds(booking: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    """
    Parse the booking's scheduled date, start_time, and end_time into timezone-aware
    UTC datetimes using the project's configured timezone.
    """
    date_str = str(booking.get("date") or "").strip()
    start_str = str(booking.get("start_time") or "").strip()
    end_str = str(booking.get("end_time") or "").strip()
    duration_minutes = int(booking.get("duration_minutes") or 60)

    if not date_str or not start_str:
        return None, None

    try:
        tz = _app_timezone()
        d_obj = date.fromisoformat(date_str)
        s_parts = [int(p) for p in start_str.split(":")[:2]]
        s_time = time(hour=s_parts[0], minute=s_parts[1])
        start_local = datetime.combine(d_obj, s_time, tzinfo=tz)

        if end_str:
            e_parts = [int(p) for p in end_str.split(":")[:2]]
            e_time = time(hour=e_parts[0], minute=e_parts[1])
            end_local = datetime.combine(d_obj, e_time, tzinfo=tz)
            if end_local <= start_local:
                end_local = start_local + timedelta(minutes=max(15, duration_minutes))
        else:
            end_local = start_local + timedelta(minutes=max(15, duration_minutes))

        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
    except Exception:
        return None, None


def authorize_booking_participant(
    booking: dict[str, Any] | None,
    user: dict[str, Any] | None,
) -> tuple[str, ObjectId]:
    """
    Verify that the authenticated user is either the assigned mentor or assigned student
    for the given booking.
    Returns (participant_role, user_object_id) where participant_role is 'mentor' or 'student'.
    Raises ClassroomPermissionError with a generic message to avoid leaking booking existence.
    """
    if not booking or not isinstance(user, dict) or not user.get("_id"):
        raise ClassroomPermissionError("Classroom session not found or access denied.")

    uid = parse_object_id(user.get("_id"))
    mentor_id = booking.get("mentor_id")
    student_id = booking.get("student_id")

    if mentor_id and (parse_object_id(mentor_id) == uid or str(mentor_id) == str(uid)):
        return "mentor", uid
    if student_id and (parse_object_id(student_id) == uid or str(student_id) == str(uid)):
        return "student", uid
    if str(user.get("role", "")).lower().strip() == "admin":
        return "mentor", uid

    raise ClassroomPermissionError("Classroom session not found or access denied.")


def evaluate_classroom_access(
    booking: dict[str, Any] | None,
    user: dict[str, Any] | str | None = None,
    *,
    participant_role: str | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """
    Authoritative evaluation of whether the user can enter the LiveKit classroom for a booking.
    Checks participant identity, session type, booking status, and schedule window.
    """
    if isinstance(user, str) and not participant_role:
        participant_role = user
        user = None

    if user is not None:
        participant_role, uid = authorize_booking_participant(booking, user)
    else:
        if not booking or participant_role not in {"mentor", "student"}:
            raise ClassroomPermissionError("Classroom session not found or access denied.")
        uid = parse_object_id(
            booking.get("mentor_id") if participant_role == "mentor" else booking.get("student_id")
        )
    assert booking is not None

    def _enrich(res: dict[str, Any]) -> dict[str, Any]:
        res.setdefault("reason_code", res.get("state", ""))
        res.setdefault("message", res.get("reason", ""))
        res.setdefault("can_mentor_open_early", False)
        return res

    session_type = (booking.get("session_type") or "online").strip().lower()
    if session_type != "online":
        return _enrich({
            "can_join": False,
            "state": "in_person",
            "classroom_status": CLASSROOM_STATUS_NOT_APPLICABLE,
            "participant_role": participant_role,
            "user_id": str(uid),
            "reason": "This booking is an in-person session and does not use an online video classroom.",
            "schedule_start_utc": None,
            "schedule_end_utc": None,
            "window_open_utc": None,
            "window_close_utc": None,
        })

    status = (booking.get("status") or "").strip().lower()
    if status == "completed" or booking.get("classroom_status") == CLASSROOM_STATUS_COMPLETED:
        return _enrich({
            "can_join": False,
            "state": "completed",
            "classroom_status": CLASSROOM_STATUS_COMPLETED,
            "participant_role": participant_role,
            "user_id": str(uid),
            "reason": "This mentorship session has already been completed and closed.",
            "schedule_start_utc": None,
            "schedule_end_utc": None,
            "window_open_utc": None,
            "window_close_utc": None,
        })

    if status == "cancelled":
        return _enrich({
            "can_join": False,
            "state": "cancelled",
            "classroom_status": CLASSROOM_STATUS_CANCELLED,
            "participant_role": participant_role,
            "user_id": str(uid),
            "reason": "This session has been cancelled and the virtual classroom is closed.",
            "schedule_start_utc": None,
            "schedule_end_utc": None,
            "window_open_utc": None,
            "window_close_utc": None,
        })

    if status == "rejected":
        return _enrich({
            "can_join": False,
            "state": "rejected",
            "classroom_status": CLASSROOM_STATUS_REJECTED,
            "participant_role": participant_role,
            "user_id": str(uid),
            "reason": "This booking request was declined.",
            "schedule_start_utc": None,
            "schedule_end_utc": None,
            "window_open_utc": None,
            "window_close_utc": None,
        })

    if status == "no_show":
        return _enrich({
            "can_join": False,
            "state": "no_show",
            "classroom_status": CLASSROOM_STATUS_NO_SHOW,
            "participant_role": participant_role,
            "user_id": str(uid),
            "reason": "This session was marked as a no-show and can no longer be joined.",
            "schedule_start_utc": None,
            "schedule_end_utc": None,
            "window_open_utc": None,
            "window_close_utc": None,
        })

    if status in {"pending", "rescheduled_pending"}:
        return _enrich({
            "can_join": False,
            "state": "pending",
            "classroom_status": (
                CLASSROOM_STATUS_RESCHEDULE_PENDING
                if status == "rescheduled_pending"
                else CLASSROOM_STATUS_REQUESTED
            ),
            "participant_role": participant_role,
            "user_id": str(uid),
            "reason": "This booking has not been confirmed yet. The classroom will unlock once the schedule is accepted.",
            "schedule_start_utc": None,
            "schedule_end_utc": None,
            "window_open_utc": None,
            "window_close_utc": None,
        })

    if status != "accepted":
        return _enrich({
            "can_join": False,
            "state": "invalid_status",
            "classroom_status": status,
            "participant_role": participant_role,
            "user_id": str(uid),
            "reason": "This booking is not in an active confirmed state.",
            "schedule_start_utc": None,
            "schedule_end_utc": None,
            "window_open_utc": None,
            "window_close_utc": None,
        })

    now_utc = reference_time or _now()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    early_minutes = int(
        getattr(settings, "CLASSROOM_EARLY_JOIN_MINUTES", DEFAULT_EARLY_JOIN_MINUTES)
        or DEFAULT_EARLY_JOIN_MINUTES
    )
    grace_minutes = int(
        getattr(settings, "CLASSROOM_LATE_JOIN_GRACE_MINUTES", DEFAULT_LATE_GRACE_MINUTES)
        or DEFAULT_LATE_GRACE_MINUTES
    )

    start_utc, end_utc = get_booking_schedule_bounds(booking)
    if start_utc and end_utc:
        window_open_utc = start_utc - timedelta(minutes=early_minutes)
        window_close_utc = end_utc + timedelta(minutes=grace_minutes)

        is_in_progress = booking.get("classroom_status") == CLASSROOM_STATUS_IN_PROGRESS
        mentor_opened_early = bool(booking.get("mentor_opened_early", False))

        if now_utc < window_open_utc and not is_in_progress and not mentor_opened_early:
            delta_minutes = max(1, int((window_open_utc - now_utc).total_seconds() // 60))
            return _enrich({
                "can_join": False,
                "state": "too_early",
                "classroom_status": CLASSROOM_STATUS_SCHEDULED,
                "participant_role": participant_role,
                "user_id": str(uid),
                "can_mentor_open_early": participant_role == "mentor",
                "reason": (
                    f"The virtual classroom opens {early_minutes} minutes before the scheduled start time "
                    f"({booking.get('date')} at {booking.get('start_time')} {get_timezone_label()})."
                ),
                "minutes_until_open": delta_minutes,
                "schedule_start_utc": start_utc,
                "schedule_end_utc": end_utc,
                "window_open_utc": window_open_utc,
                "window_close_utc": window_close_utc,
            })

        if now_utc > window_close_utc and not is_in_progress:
            return _enrich({
                "can_join": False,
                "state": "expired",
                "classroom_status": CLASSROOM_STATUS_EXPIRED,
                "participant_role": participant_role,
                "user_id": str(uid),
                "reason": (
                    f"The scheduled join window for this session ({booking.get('date')} "
                    f"{booking.get('start_time')}–{booking.get('end_time')}) has passed."
                ),
                "schedule_start_utc": start_utc,
                "schedule_end_utc": end_utc,
                "window_open_utc": window_open_utc,
                "window_close_utc": window_close_utc,
            })
    else:
        window_open_utc = None
        window_close_utc = None

    effective_cls_status = (
        CLASSROOM_STATUS_IN_PROGRESS
        if booking.get("classroom_status") == CLASSROOM_STATUS_IN_PROGRESS
        else CLASSROOM_STATUS_READY
    )
    return _enrich({
        "can_join": True,
        "state": "in_progress" if effective_cls_status == CLASSROOM_STATUS_IN_PROGRESS else "ready",
        "classroom_status": effective_cls_status,
        "participant_role": participant_role,
        "user_id": str(uid),
        "reason": "Classroom is open and ready to join.",
        "schedule_start_utc": start_utc,
        "schedule_end_utc": end_utc,
        "window_open_utc": window_open_utc,
        "window_close_utc": window_close_utc,
    })


def mentor_unlock_classroom_early(
    *,
    booking_id: Any,
    mentor_user: dict[str, Any] | None = None,
    user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Allow the assigned mentor of an accepted online booking to unlock the classroom early
    (e.g., for early preparation or immediate live session start).
    """
    actor = mentor_user or user
    bid = parse_object_id(booking_id)
    booking = bookings_collection.find_one({"_id": bid})
    role, uid = authorize_booking_participant(booking, actor)
    if role != "mentor":
        raise ClassroomPermissionError("Only the assigned mentor can open the classroom early.")
    assert booking is not None

    if booking.get("session_type") != "online":
        raise ClassroomStateError("In-person bookings do not have an online classroom.", state_code="in_person")
    if booking.get("status") != "accepted":
        raise ClassroomStateError("Only accepted bookings can be opened.", state_code="not_accepted")

    now = _now()
    room_name = ensure_booking_room_name(booking)
    bookings_collection.update_one(
        {"_id": bid},
        {
            "$set": {
                "mentor_opened_early": True,
                "classroom_status": CLASSROOM_STATUS_READY,
                "updated_at": now,
            }
        },
    )
    _log_classroom_event(
        booking_id=bid,
        event_type="mentor_opened_early",
        actor_role="mentor",
        actor_id=uid,
        metadata={"room_name": room_name},
    )
    return {"status": "ok", "room_name": room_name, "classroom_status": CLASSROOM_STATUS_READY}


# ---------------------------------------------------------------------------
# LiveKit Token Issuance (Server-Side Only)
# ---------------------------------------------------------------------------

def generate_classroom_token(
    *,
    booking_id: Any,
    user: dict[str, Any],
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """
    Validate participant authorization, booking status, schedule window, and LiveKit config,
    then issue a short-lived, role-scoped LiveKit JWT using the official livekit-api SDK.
    Never exposes LIVEKIT_API_SECRET or LIVEKIT_API_KEY to the caller.
    """
    bid = parse_object_id(booking_id)
    booking = bookings_collection.find_one({"_id": bid})
    access = evaluate_classroom_access(booking, user, reference_time=reference_time)
    assert booking is not None

    if not access["can_join"]:
        raise ClassroomStateError(access["reason"], state_code=access["state"])

    participant_role = access["participant_role"]
    uid_str = access["user_id"]

    check_rate_limit(f"token:{uid_str}:{bid}", limit=15, window_seconds=60)

    lk_cfg = validate_livekit_config()
    room_name = ensure_booking_room_name(booking)

    if participant_role == "mentor":
        participant_identity = f"mentor:{uid_str}"
        display_name = (
            booking.get("mentor_name")
            or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
            or "Mentor"
        )
        grants = lk_api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            can_update_own_metadata=True,
            room_admin=True,
            room_create=True,
            room_record=False,
        )
    else:
        participant_identity = f"student:{uid_str}"
        display_name = (
            booking.get("student_name")
            or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
            or "Student"
        )
        grants = lk_api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            can_update_own_metadata=True,
            room_admin=False,
            room_create=False,
            room_record=False,
        )

    participant_metadata = json.dumps(
        {
            "role": participant_role,
            "booking_id": str(bid),
            "display_name": display_name,
        }
    )

    ttl_seconds = lk_cfg["ttl_seconds"]
    token_obj = (
        lk_api.AccessToken(lk_cfg["api_key"], lk_cfg["api_secret"])
        .with_identity(participant_identity)
        .with_name(display_name)
        .with_metadata(participant_metadata)
        .with_ttl(timedelta(seconds=ttl_seconds))
        .with_grants(grants)
    )
    jwt_token = token_obj.to_jwt()

    _log_classroom_event(
        booking_id=bid,
        event_type="token_issued",
        actor_role=participant_role,
        actor_id=parse_object_id(uid_str),
        metadata={
            "participant_identity": participant_identity,
            "room_name": room_name,
            "ttl_seconds": ttl_seconds,
        },
    )

    return {
        "token": jwt_token,
        "livekit_url": lk_cfg["url"],
        "ws_url": lk_cfg["url"],
        "room_name": room_name,
        "participant_identity": participant_identity,
        "participant_name": display_name,
        "participant_role": participant_role,
        "expires_in": ttl_seconds,
        "booking_id": str(bid),
    }


# ---------------------------------------------------------------------------
# Email Invitation Service (Brevo Integration & Idempotent Retry)
# ---------------------------------------------------------------------------

def send_classroom_invitation_email(
    booking_id: Any,
    *,
    is_reschedule: bool = False,
    is_retry: bool = False,
    force_resend: bool = False,
    triggered_by_user: dict[str, Any] | None = None,
    actor_user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Send or retry the online classroom confirmation & invitation email to the student's
    registered email address after a booking has been accepted and persisted.
    Never emails a LiveKit token or secret; sends the authenticated SkillForge classroom URL.
    """
    if force_resend:
        is_retry = True
    if actor_user is not None and triggered_by_user is None:
        triggered_by_user = actor_user

    bid = parse_object_id(booking_id)
    booking = bookings_collection.find_one({"_id": bid})
    if not booking:
        raise ClassroomPermissionError("Booking not found.")

    if triggered_by_user is not None:
        if not isinstance(triggered_by_user, dict):
            raise ClassroomPermissionError("Authentication required.")
        user_role = triggered_by_user.get("role")
        uid = parse_object_id(triggered_by_user.get("_id"))
        if user_role != "admin" and (user_role != "mentor" or parse_object_id(booking.get("mentor_id")) != uid):
            raise ClassroomPermissionError("Only the assigned mentor or an administrator can retry invitations.")
        check_rate_limit(f"invite_retry:{uid}:{bid}", limit=5, window_seconds=60)

    if booking.get("session_type") != "online":
        bookings_collection.update_one(
            {"_id": bid},
            {"$set": {"invitation_status": INVITATION_STATUS_NOT_REQUIRED}},
        )
        return {
            "sent": False,
            "invitation_status": INVITATION_STATUS_NOT_REQUIRED,
            "reason": "In-person sessions do not use online classroom invitations.",
            "message": "In-person sessions do not use online classroom invitations.",
        }

    if booking.get("status") != "accepted":
        raise ClassroomStateError(
            "Classroom invitations can only be sent for accepted online bookings.",
            state_code="not_accepted",
        )

    # Idempotency guard: if already sent for the current schedule and not a retry or reschedule, skip duplicate
    if (
        not is_retry
        and not is_reschedule
        and booking.get("invitation_status") == INVITATION_STATUS_SENT
        and booking.get("invitation_id")
    ):
        return {
            "sent": True,
            "invitation_status": INVITATION_STATUS_SENT,
            "invitation_id": booking.get("invitation_id"),
            "already_sent": True,
            "message": "Classroom invitation email was already sent.",
        }

    room_name = ensure_booking_room_name(booking)
    classroom_path = build_classroom_path(bid)
    classroom_url = build_absolute_classroom_url(bid)

    student_id = booking.get("student_id")
    student_user = users_collection.find_one({"_id": student_id}) if student_id else None
    recipient_email = (
        (student_user.get("email") if student_user else None)
        or booking.get("student_email")
        or ""
    ).strip()
    student_name = (
        (f"{student_user.get('first_name', '')} {student_user.get('last_name', '')}".strip() if student_user else "")
        or booking.get("student_name")
        or "Student"
    )
    mentor_name = booking.get("mentor_name") or "Mentor"
    topic = booking.get("topic") or "Mentoring Session"
    tz_label = get_timezone_label()

    invitation_id = booking.get("invitation_id")
    if not invitation_id or is_reschedule:
        invitation_id = f"inv-{bid}-{uuid.uuid4().hex[:10]}"

    now = _now()
    subject = (
        f"Updated Schedule & Classroom Link — {topic} with {mentor_name}"
        if is_reschedule
        else f"Your Online Mentorship Session Is Confirmed — {topic} with {mentor_name}"
    )

    email_ok = False
    error_summary = ""
    if not recipient_email or "@" not in recipient_email:
        error_summary = "invalid_recipient_email"
    else:
        email_ok = email_service.send_templated_email(
            subject=subject,
            recipient_email=recipient_email,
            template_name_base="classroom_invitation_student",
            context={
                "email_title": subject,
                "student_name": student_name,
                "mentor_name": mentor_name,
                "topic": topic,
                "date": booking.get("date", ""),
                "start_time": booking.get("start_time", ""),
                "end_time": booking.get("end_time", ""),
                "duration_minutes": booking.get("duration_minutes", 60),
                "timezone_label": tz_label,
                "classroom_url": classroom_url,
                "action_url": classroom_path,
                "is_reschedule": is_reschedule,
                "invitation_id": invitation_id,
                "early_join_minutes": getattr(
                    settings, "CLASSROOM_EARLY_JOIN_MINUTES", DEFAULT_EARLY_JOIN_MINUTES
                ),
            },
        )
        if not email_ok:
            error_summary = "brevo_delivery_failed"

    new_inv_status = INVITATION_STATUS_SENT if email_ok else INVITATION_STATUS_FAILED
    update_doc: dict[str, Any] = {
        "livekit_room_name": room_name,
        "classroom_url": classroom_path,
        "invitation_id": invitation_id,
        "invitation_status": new_inv_status,
        "invitation_last_error": error_summary,
        "updated_at": now,
    }
    if email_ok:
        update_doc["invitation_sent_at"] = now
    if not booking.get("classroom_status") or booking.get("classroom_status") in {
        CLASSROOM_STATUS_REQUESTED,
        CLASSROOM_STATUS_RESCHEDULE_PENDING,
    }:
        update_doc["classroom_status"] = CLASSROOM_STATUS_SCHEDULED

    inc_doc: dict[str, int] = {"invitation_retry_count": 1} if is_retry else {}
    mongo_update: dict[str, Any] = {"$set": update_doc}
    if inc_doc:
        mongo_update["$inc"] = inc_doc

    bookings_collection.update_one({"_id": bid}, mongo_update)

    _log_classroom_event(
        booking_id=bid,
        event_type="invitation_sent" if email_ok else "invitation_failed",
        actor_role="mentor" if triggered_by_user else "system",
        actor_id=parse_object_id(triggered_by_user["_id"]) if triggered_by_user else None,
        metadata={
            "invitation_id": invitation_id,
            "is_reschedule": is_reschedule,
            "is_retry": is_retry,
            "error": error_summary,
        },
    )

    return {
        "sent": email_ok,
        "invitation_status": new_inv_status,
        "invitation_id": invitation_id,
        "classroom_url": classroom_path,
        "error": error_summary,
        "message": (
            f"Classroom invitation email sent to {recipient_email}."
            if email_ok
            else "Could not deliver classroom invitation email."
        ),
    }


# ---------------------------------------------------------------------------
# Attendance & Session Lifecycle Persistence
# ---------------------------------------------------------------------------

def _default_attendance() -> dict[str, Any]:
    return {
        "mentor_joined": False,
        "mentor_currently_connected": False,
        "mentor_first_joined_at": None,
        "mentor_last_joined_at": None,
        "mentor_last_left_at": None,
        "mentor_join_count": 0,
        "student_joined": False,
        "student_currently_connected": False,
        "student_first_joined_at": None,
        "student_last_joined_at": None,
        "student_last_left_at": None,
        "student_join_count": 0,
        "both_attended": False,
    }


def _is_role_currently_connected(att_dict: dict[str, Any], role_prefix: str) -> bool:
    conn_key = f"{role_prefix}_currently_connected"
    if conn_key in att_dict and att_dict[conn_key] is not None:
        return bool(att_dict[conn_key])
    if not att_dict.get(f"{role_prefix}_joined"):
        return False
    last_join = att_dict.get(f"{role_prefix}_last_joined_at")
    last_left = att_dict.get(f"{role_prefix}_last_left_at")
    if isinstance(last_join, datetime) and isinstance(last_left, datetime):
        lj = last_join if last_join.tzinfo else last_join.replace(tzinfo=timezone.utc)
        ll = last_left if last_left.tzinfo else last_left.replace(tzinfo=timezone.utc)
        if ll >= lj:
            return False
    return True


def _log_classroom_event(
    *,
    booking_id: ObjectId,
    event_type: str,
    actor_role: str,
    actor_id: ObjectId | None = None,
    external_event_id: str | None = None,
    event_timestamp: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """
    Record an auditable classroom lifecycle event in MongoDB.
    Returns False if external_event_id is a duplicate (idempotent webhook handling).
    """
    now = event_timestamp or _now()
    doc: dict[str, Any] = {
        "_id": ObjectId(),
        "booking_id": booking_id,
        "event_type": event_type,
        "actor_role": actor_role,
        "actor_id": actor_id,
        "metadata": metadata or {},
        "created_at": now,
    }
    if external_event_id:
        doc["external_event_id"] = external_event_id

    try:
        classroom_events_collection.insert_one(doc)
        return True
    except DuplicateKeyError:
        return False
    except PyMongoError as error:
        logger.warning("Failed to log classroom event %s for booking %s: %s", event_type, booking_id, error)
        return False


def record_attendance_event(
    *,
    booking_id: Any,
    user: dict[str, Any] | None = None,
    participant_role: str | None = None,
    event_type: str,
    external_event_id: str | None = None,
    event_timestamp: datetime | None = None,
    source: str = "classroom_session",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Record a verified participant attendance transition ('participant_joined' or 'participant_left').
    - Distinguishes lobby visits from actual LiveKit room connection.
    - Never marks a session completed when a participant disconnects.
    - Handles duplicate and out-of-order events safely.
    """
    normalized_map = {
        "join": "participant_joined",
        "participant_joined": "participant_joined",
        "leave": "participant_left",
        "participant_left": "participant_left",
        "reconnect": "participant_joined",
        "heartbeat": "lobby_visited",
        "lobby_visited": "lobby_visited",
    }
    clean_event = normalized_map.get((event_type or "").strip().lower())
    if not clean_event:
        raise ClassroomStateError(f"Unsupported attendance event: {event_type}")

    bid = parse_object_id(booking_id)
    booking = bookings_collection.find_one({"_id": bid})
    if not booking:
        raise ClassroomPermissionError("Booking not found.")

    actor_id: ObjectId | None = None
    if user is not None:
        role, uid = authorize_booking_participant(booking, user)
        participant_role = role
        actor_id = uid
    elif participant_role not in {"mentor", "student"}:
        raise ClassroomPermissionError("Invalid participant role.")

    now = event_timestamp or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    event_meta = {"source": source, "raw_event": event_type}
    if metadata:
        event_meta.update(metadata)

    # Deduplicate by external_event_id if provided (e.g. LiveKit webhook or client event token)
    inserted = _log_classroom_event(
        booking_id=bid,
        event_type=clean_event,
        actor_role=participant_role,
        actor_id=actor_id,
        external_event_id=external_event_id,
        event_timestamp=now,
        metadata=event_meta,
    )

    def _build_role_summaries(att_dict: dict[str, Any]) -> dict[str, Any]:
        return {
            "mentor": {
                "currently_connected": _is_role_currently_connected(att_dict, "mentor"),
                "joined": bool(att_dict.get("mentor_joined")),
                "join_count": int(att_dict.get("mentor_join_count") or 0),
                "first_joined_at": att_dict.get("mentor_first_joined_at"),
                "last_left_at": att_dict.get("mentor_last_left_at"),
            },
            "student": {
                "currently_connected": _is_role_currently_connected(att_dict, "student"),
                "joined": bool(att_dict.get("student_joined")),
                "join_count": int(att_dict.get("student_join_count") or 0),
                "first_joined_at": att_dict.get("student_first_joined_at"),
                "last_left_at": att_dict.get("student_last_left_at"),
            },
        }

    if external_event_id and not inserted:
        existing_att = booking.get("attendance") or _default_attendance()
        return {
            "recorded": False,
            "duplicate": True,
            "attendance": existing_att,
            **_build_role_summaries(existing_att),
            "classroom_status": booking.get("classroom_status", CLASSROOM_STATUS_SCHEDULED),
        }

    # Lobby visits / heartbeats are logged for diagnostics but NEVER count as session attendance
    if clean_event == "lobby_visited":
        existing_att = booking.get("attendance") or _default_attendance()
        return {
            "recorded": True,
            "duplicate": False,
            "attendance": existing_att,
            **_build_role_summaries(existing_att),
            "classroom_status": booking.get("classroom_status", CLASSROOM_STATUS_SCHEDULED),
        }

    att = dict(_default_attendance())
    if isinstance(booking.get("attendance"), dict):
        att.update(booking["attendance"])

    role_prefix = "mentor" if participant_role == "mentor" else "student"
    update_fields: dict[str, Any] = {"updated_at": _now()}

    if clean_event == "participant_joined":
        att[f"{role_prefix}_joined"] = True
        att[f"{role_prefix}_currently_connected"] = True
        if not att.get(f"{role_prefix}_first_joined_at"):
            att[f"{role_prefix}_first_joined_at"] = now
        prev_join = att.get(f"{role_prefix}_last_joined_at")
        if isinstance(prev_join, datetime) and prev_join.tzinfo is None:
            prev_join = prev_join.replace(tzinfo=timezone.utc)
        if not prev_join or now >= prev_join:
            att[f"{role_prefix}_last_joined_at"] = now
        att[f"{role_prefix}_join_count"] = int(att.get(f"{role_prefix}_join_count") or 0) + 1
        att["both_attended"] = bool(att.get("mentor_joined") and att.get("student_joined"))

        if not booking.get("meeting_started_at"):
            update_fields["meeting_started_at"] = now
        if booking.get("status") == "accepted" and booking.get("classroom_status") != CLASSROOM_STATUS_COMPLETED:
            update_fields["classroom_status"] = CLASSROOM_STATUS_IN_PROGRESS

    elif clean_event == "participant_left":
        att[f"{role_prefix}_currently_connected"] = False
        prev_left = att.get(f"{role_prefix}_last_left_at")
        if isinstance(prev_left, datetime) and prev_left.tzinfo is None:
            prev_left = prev_left.replace(tzinfo=timezone.utc)
        if not prev_left or now >= prev_left:
            att[f"{role_prefix}_last_left_at"] = now

    update_fields["attendance"] = att
    if att.get("both_attended"):
        update_fields["attendance_status"] = "attended"

    bookings_collection.update_one({"_id": bid}, {"$set": update_fields})
    return {
        "recorded": True,
        "duplicate": False,
        "attendance": att,
        **_build_role_summaries(att),
        "classroom_status": update_fields.get(
            "classroom_status", booking.get("classroom_status", CLASSROOM_STATUS_SCHEDULED)
        ),
    }


def process_livekit_webhook(*, raw_body: str, auth_header: str) -> dict[str, Any]:
    """
    Verify and process an incoming LiveKit webhook using the official livekit.api.WebhookReceiver.
    Rejects invalid signatures and handles duplicate or out-of-order deliveries safely.
    """
    if not auth_header or not raw_body:
        raise ClassroomWebhookSignatureError("Missing webhook signature or payload.")

    lk_cfg = validate_livekit_config()
    try:
        verifier = lk_api.TokenVerifier(lk_cfg["api_key"], lk_cfg["api_secret"])
        receiver = lk_api.WebhookReceiver(verifier)
        clean_token = auth_header.strip()
        if clean_token.lower().startswith("bearer "):
            clean_token = clean_token[7:].strip()
        event = receiver.receive(raw_body, clean_token)
    except Exception as error:
        raise ClassroomWebhookSignatureError("Invalid LiveKit webhook signature.") from error

    event_name = getattr(event, "event", "") or ""
    event_id = getattr(event, "id", "") or None
    room_obj = getattr(event, "room", None)
    room_name = getattr(room_obj, "name", "") if room_obj else ""
    participant_obj = getattr(event, "participant", None)
    identity = getattr(participant_obj, "identity", "") if participant_obj else ""
    created_at_sec = getattr(event, "created_at", 0) or 0
    event_ts = (
        datetime.fromtimestamp(created_at_sec, tz=timezone.utc)
        if created_at_sec > 0
        else _now()
    )

    if not room_name:
        return {"processed": False, "reason": "no_room_name"}

    booking = bookings_collection.find_one({"livekit_room_name": room_name})
    if not booking:
        return {"processed": False, "reason": "booking_not_found"}

    role = None
    if identity.startswith("mentor:"):
        role = "mentor"
    elif identity.startswith("student:"):
        role = "student"

    if event_name == "participant_joined" and role:
        res = record_attendance_event(
            booking_id=booking["_id"],
            participant_role=role,
            event_type="participant_joined",
            external_event_id=event_id,
            event_timestamp=event_ts,
            source="livekit_webhook",
        )
        return {"processed": True, "event": event_name, "duplicate": res["duplicate"]}

    if event_name == "participant_left" and role:
        res = record_attendance_event(
            booking_id=booking["_id"],
            participant_role=role,
            event_type="participant_left",
            external_event_id=event_id,
            event_timestamp=event_ts,
            source="livekit_webhook",
        )
        return {"processed": True, "event": event_name, "duplicate": res["duplicate"]}

    # Log room_started / room_finished without auto-completing the booking
    inserted = _log_classroom_event(
        booking_id=booking["_id"],
        event_type=f"webhook_{event_name or 'unknown'}",
        actor_role="system",
        external_event_id=event_id,
        event_timestamp=event_ts,
        metadata={"room_name": room_name},
    )
    return {"processed": True, "event": event_name, "duplicate": not inserted}


# ---------------------------------------------------------------------------
# In-Meeting Chat & Moderation Controls
# ---------------------------------------------------------------------------

def list_chat_messages(booking_id: Any, *, user: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    bid = parse_object_id(booking_id)
    booking = bookings_collection.find_one({"_id": bid})
    authorize_booking_participant(booking, user)

    cursor = (
        classroom_messages_collection.find({"booking_id": bid})
        .sort("created_at", 1)
        .limit(max(1, min(limit, 200)))
    )
    messages_out: list[dict[str, Any]] = []
    for doc in cursor:
        created_at = doc.get("created_at")
        messages_out.append({
            "id": str(doc["_id"]),
            "sender_id": str(doc.get("sender_id", "")),
            "sender_name": doc.get("sender_name", "Participant"),
            "sender_role": doc.get("sender_role", "student"),
            "message": doc.get("message", ""),
            "created_at": created_at.isoformat() if isinstance(created_at, datetime) else "",
            "time_display": created_at.strftime("%H:%M") if isinstance(created_at, datetime) else "",
        })
    return messages_out


def post_chat_message(
    *,
    booking_id: Any,
    user: dict[str, Any],
    message_text: str,
) -> dict[str, Any]:
    bid = parse_object_id(booking_id)
    booking = bookings_collection.find_one({"_id": bid})
    role, uid = authorize_booking_participant(booking, user)
    assert booking is not None

    if booking.get("session_type") != "online" or booking.get("status") not in {"accepted", "completed"}:
        raise ClassroomStateError("Chat is only available for active confirmed online sessions.")
    if booking.get("status") == "completed":
        raise ClassroomStateError("This session has ended; chat is now read-only.")

    check_rate_limit(f"chat:{uid}:{bid}", limit=30, window_seconds=60)

    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", (message_text or "")).strip()
    if not cleaned:
        raise ClassroomStateError("Message cannot be empty.")
    if len(cleaned) > MAX_CHAT_MESSAGE_LENGTH:
        raise ClassroomStateError(f"Chat message must be {MAX_CHAT_MESSAGE_LENGTH} characters or fewer.")

    sender_name = (
        (booking.get("mentor_name") if role == "mentor" else booking.get("student_name"))
        or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        or role.title()
    )
    now = _now()
    doc: dict[str, Any] = {
        "_id": ObjectId(),
        "booking_id": bid,
        "sender_id": uid,
        "sender_name": sender_name,
        "sender_role": role,
        "message": cleaned,
        "created_at": now,
    }
    classroom_messages_collection.insert_one(doc)
    return {
        "id": str(doc["_id"]),
        "sender_id": str(uid),
        "sender_name": sender_name,
        "sender_role": role,
        "message": cleaned,
        "created_at": now.isoformat(),
        "time_display": now.strftime("%H:%M"),
    }


def record_moderation_action(
    *,
    booking_id: Any,
    mentor_user: dict[str, Any] | None = None,
    user: dict[str, Any] | None = None,
    action: str,
    target_identity: str = "",
) -> dict[str, Any]:
    """
    Validate and record a mentor-only classroom moderation action
    (e.g. 'request_mute_student', 'lower_student_hand', 'open_early').
    """
    actor = mentor_user or user
    bid = parse_object_id(booking_id)
    booking = bookings_collection.find_one({"_id": bid})
    role, uid = authorize_booking_participant(booking, actor)
    if role != "mentor":
        raise ClassroomPermissionError("Only the assigned mentor can perform classroom moderation actions.")
    assert booking is not None

    action_aliases = {
        "mute_student_request": "request_mute_student",
        "request_mute_student": "request_mute_student",
        "lower_hand": "lower_student_hand",
        "lower_student_hand": "lower_student_hand",
        "open_early": "open_early",
        "unlock_early": "open_early",
        "clear_raised_hands": "clear_raised_hands",
    }
    clean_action = action_aliases.get((action or "").strip().lower(), "")
    if not clean_action:
        raise ClassroomStateError(f"Unsupported moderation action: {action}")

    if clean_action == "open_early":
        return mentor_unlock_classroom_early(booking_id=bid, mentor_user=actor)

    now = _now()
    mod_Signal = {
        "id": str(ObjectId()),
        "action": clean_action,
        "target_identity": target_identity[:80],
        "created_at": now.isoformat(),
    }
    try:
        bookings_collection.update_one(
            {"_id": bid},
            {"$set": {"last_moderation_signal": mod_Signal, "updated_at": now}},
        )
    except Exception:
        pass

    _log_classroom_event(
        booking_id=bid,
        event_type=f"moderation_{clean_action}",
        actor_role="mentor",
        actor_id=uid,
        metadata={"target_identity": target_identity[:80]},
    )
    return {
        "status": "ok",
        "action": clean_action,
        "target_identity": target_identity[:80],
        "signal_id": mod_Signal["id"],
    }


# ---------------------------------------------------------------------------
# Classroom Metadata Snapshot
# ---------------------------------------------------------------------------

def get_classroom_metadata(
    *,
    booking_id: Any,
    user: dict[str, Any],
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """
    Return safe, authorized session metadata for the classroom lobby and active call UI.
    Never exposes API keys, secrets, or tokens.
    """
    bid = parse_object_id(booking_id)
    booking = bookings_collection.find_one({"_id": bid})
    if not booking:
        raise ClassroomNotFoundError("Mentorship session not found.")
    access = evaluate_classroom_access(booking, user, reference_time=reference_time)
    assert booking is not None

    lk_cfg = get_livekit_config()
    att = dict(_default_attendance())
    if isinstance(booking.get("attendance"), dict):
        att.update(booking["attendance"])

    mentor_doc = users_collection.find_one({"_id": booking.get("mentor_id")}) if booking.get("mentor_id") else None
    student_doc = users_collection.find_one({"_id": booking.get("student_id")}) if booking.get("student_id") else None

    mentor_name = (
        booking.get("mentor_name")
        or (f"{mentor_doc.get('first_name', '')} {mentor_doc.get('last_name', '')}".strip() if mentor_doc else "")
        or "Mentor"
    )
    student_name = (
        booking.get("student_name")
        or (f"{student_doc.get('first_name', '')} {student_doc.get('last_name', '')}".strip() if student_doc else "")
        or "Student"
    )
    participant_name = mentor_name if access["participant_role"] == "mentor" else student_name
    room_name = booking.get("livekit_room_name") or generate_stable_room_name(bid)

    try:
        chat_history = list_chat_messages(bid, user=user, limit=100)
    except Exception:
        chat_history = []

    config_msg = ""
    if not lk_cfg["is_configured"]:
        config_msg = f"Missing or placeholder settings: {', '.join(lk_cfg['missing_settings'])}"

    return {
        "booking_id": str(bid),
        "room_name": room_name,
        "topic": booking.get("topic", "Mentorship Session"),
        "student_goal": booking.get("student_goal") or booking.get("learning_objective", ""),
        "learning_objective": booking.get("learning_objective", ""),
        "date": booking.get("date", ""),
        "start_time": booking.get("start_time", ""),
        "end_time": booking.get("end_time", ""),
        "duration_minutes": booking.get("duration_minutes", 60),
        "timezone": get_timezone_label(),
        "session_type": booking.get("session_type", "online"),
        "booking_status": booking.get("status", ""),
        "classroom_status": access["classroom_status"],
        "state": access["state"],
        "can_join": access["can_join"],
        "reason": access["reason"],
        "can_mentor_open_early": bool(access.get("can_mentor_open_early", False)),
        "participant_role": access["participant_role"],
        "participant_name": participant_name,
        "mentor_name": mentor_name,
        "student_name": student_name,
        "mentor": {
            "id": str(booking.get("mentor_id", "")),
            "name": mentor_name,
        },
        "student": {
            "id": str(booking.get("student_id", "")),
            "name": student_name,
        },
        "access": {
            "can_join": access["can_join"],
            "reason_code": access["state"],
            "message": access["reason"],
            "can_mentor_open_early": bool(access.get("can_mentor_open_early", False)),
        },
        "invitation_status": booking.get("invitation_status", INVITATION_STATUS_PENDING),
        "livekit_configured": lk_cfg["is_configured"],
        "livekit_missing_settings": lk_cfg["missing_settings"],
        "livekit_config_message": config_msg,
        "chat_messages": chat_history,
        "last_moderation_signal": booking.get("last_moderation_signal"),
        "student_review_url": f"/users/mentors/reviews/{bid}/add/",
        "mentor_sessions_url": "/users/backoffice/mentors/sessions/",
        "attendance": {
            "mentor_joined": bool(att.get("mentor_joined")),
            "student_joined": bool(att.get("student_joined")),
            "both_attended": bool(att.get("both_attended")),
            "mentor_join_count": int(att.get("mentor_join_count") or 0),
            "student_join_count": int(att.get("student_join_count") or 0),
            "mentor": {
                "currently_connected": (
                    False
                    if booking.get("status") == "completed"
                    else _is_role_currently_connected(att, "mentor")
                ),
                "joined": bool(att.get("mentor_joined")),
                "join_count": int(att.get("mentor_join_count") or 0),
            },
            "student": {
                "currently_connected": (
                    False
                    if booking.get("status") == "completed"
                    else _is_role_currently_connected(att, "student")
                ),
                "joined": bool(att.get("student_joined")),
                "join_count": int(att.get("student_join_count") or 0),
            },
        },
    }


# ---------------------------------------------------------------------------
# Optional Groq AI Classroom Learning Assistant
# ---------------------------------------------------------------------------

def ask_classroom_ai_assistant(
    *,
    booking_id: Any,
    user: dict[str, Any],
    mode: str = "",
    prompt_type: str = "",
    prompt_text: str = "",
    user_input: str = "",
) -> dict[str, Any]:
    """
    Isolated AI study & mentoring assistant inside the classroom reusing the existing
    Groq configuration. Falls back cleanly if Groq is unavailable or rate-limited so
    that audio/video classroom functionality is never impacted.
    """
    effective_mode = mode or prompt_type or "explain_concept"
    effective_prompt = prompt_text or user_input or ""

    bid = parse_object_id(booking_id)
    booking = bookings_collection.find_one({"_id": bid})
    role, uid = authorize_booking_participant(booking, user)
    assert booking is not None

    check_rate_limit(f"ai_assist:{uid}:{bid}", limit=10, window_seconds=60)

    clean_mode = (effective_mode or "explain_concept").strip().lower()
    if clean_mode not in {"explain_concept", "summarize_notes", "practice_questions", "follow_up_questions"}:
        clean_mode = "explain_concept"

    cleaned_prompt, _ = sanitize_student_goal(effective_prompt)
    cleaned_prompt = cleaned_prompt[:MAX_AI_PROMPT_LENGTH].strip()
    topic = booking.get("topic", "Software Engineering")
    objective = booking.get("learning_objective", "")

    if not cleaned_prompt:
        cleaned_prompt = f"{topic}: {objective}" if objective else topic

    mode_instructions = {
        "explain_concept": "Provide a clear, concise explanation with a short practical example.",
        "summarize_notes": "Organize the provided notes or topic into 3-5 clear bullet-point takeaways.",
        "practice_questions": "Generate 3 focused practice questions with brief answer hints.",
        "follow_up_questions": "Suggest 3 insightful follow-up questions the student can ask their mentor during this live session.",
    }

    groq_cfg = get_groq_config()
    if groq_cfg["is_configured"]:
        try:
            system_msg = (
                "You are the SkillForge Classroom Study Assistant helping during a live 1-on-1 mentorship session. "
                "Note: You only have access to text explicitly typed into this assistant panel (never live audio, video, or screen streams). "
                "Keep your response concise (under 180 words), practical, and formatted in clean plain text with bullet points."
            )
            user_msg = (
                f"Session Topic: {topic}\n"
                f"Learning Goal: {objective}\n"
                f"Task: {mode_instructions[clean_mode]}\n"
                f"User Input: {cleaned_prompt}"
            )
            payload = json.dumps({
                "model": groq_cfg["model"],
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.3,
                "max_tokens": 350,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{groq_cfg['base_url']}/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {groq_cfg['api_key']}",
                    "Content-Type": "application/json",
                    "User-Agent": "SkillForge-Classroom/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=groq_cfg["timeout"]) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                answer = (
                    resp_data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if answer:
                    return {
                        "ok": True,
                        "response": answer,
                        "answer": answer,
                        "mode": clean_mode,
                        "ai_used": True,
                        "model": groq_cfg["model"],
                    }
        except Exception as err:
            logger.warning("Classroom AI assistant fallback triggered: %s", type(err).__name__)

    # Deterministic structured fallback when Groq API is unavailable or rate-limited
    fallback_map = {
        "explain_concept": (
            f"Key Concept Breakdown — {topic}:\n"
            f"• Core Idea: Break '{cleaned_prompt[:90]}' into inputs, state changes, and expected outputs.\n"
            f"• Best Practice: Verify assumptions with a minimal reproducible example first.\n"
            f"• Tip for this session: Share your screen or paste the snippet in chat to walk through it step-by-step."
        ),
        "summarize_notes": (
            f"Session Summary Takeaways ({topic}):\n"
            f"• Objective addressed: {objective or cleaned_prompt[:100]}\n"
            f"• Key focus area: {cleaned_prompt[:120]}\n"
            f"• Next action item: Apply the discussed pattern in your project and test edge cases."
        ),
        "practice_questions": (
            f"Practice Exercises for {topic}:\n"
            f"1. How would you implement '{cleaned_prompt[:60]}' from scratch and what edge cases could fail?\n"
            f"2. What trade-offs exist between readability and performance in this approach?\n"
            f"3. How would you write an automated unit test to verify the behavior?"
        ),
        "follow_up_questions": (
            f"Suggested Questions to Ask Your Mentor:\n"
            f"1. What is the most common pitfall you see engineers make with {topic}?\n"
            f"2. How would you structure '{cleaned_prompt[:60]}' in a production codebase?\n"
            f"3. What should I practice next before our next coaching session?"
        ),
    }
    return {
        "ok": True,
        "response": fallback_map[clean_mode],
        "answer": fallback_map[clean_mode],
        "mode": clean_mode,
        "ai_used": False,
        "model": "deterministic-study-guide",
    }
