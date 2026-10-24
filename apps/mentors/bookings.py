from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse

from bson import ObjectId
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from config.mongodb import db
from . import email_service, notifications, repositories
from .services import (
    MIN_DURATION_MINUTES,
    calculate_duration_minutes,
    current_local_date,
    current_local_datetime,
    parse_date_value,
    parse_time_value,
)

logger = logging.getLogger(__name__)

bookings_collection: Collection[dict[str, Any]] = db["mentorship_bookings"]

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"
STATUS_RESCHEDULED_PENDING = "rescheduled_pending"
STATUS_COMPLETED = "completed"
STATUS_NO_SHOW = "no_show"

ALL_STATUSES = {
    STATUS_PENDING,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    STATUS_CANCELLED,
    STATUS_RESCHEDULED_PENDING,
    STATUS_COMPLETED,
    STATUS_NO_SHOW,
}

STATUS_LABELS = {
    STATUS_PENDING: "Pending",
    STATUS_ACCEPTED: "Confirmed",
    STATUS_REJECTED: "Declined",
    STATUS_CANCELLED: "Cancelled",
    STATUS_RESCHEDULED_PENDING: "Reschedule Proposed",
    STATUS_COMPLETED: "Completed",
    STATUS_NO_SHOW: "No-Show",
}


class BookingError(Exception):
    """Base exception for booking operations."""


class BookingValidationError(BookingError):
    """Raised on invalid user input or business rule violation."""


class BookingNotFoundError(BookingError):
    """Raised when a booking record cannot be located."""


class BookingPermissionError(BookingError):
    """Raised on unauthorized booking access."""


class SlotConflictError(BookingError):
    """Raised when the selected availability slot is already reserved."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_object_id(value: Any) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    raise BookingValidationError(f"Invalid identifier: {value}")


def create_indexes() -> None:
    try:
        bookings_collection.create_index(
            [("student_id", 1), ("created_at", -1)],
            name="bookings_student_recent",
        )
        bookings_collection.create_index(
            [("mentor_id", 1), ("status", 1), ("date", 1)],
            name="bookings_mentor_status",
        )
        bookings_collection.create_index(
            [("availability_id", 1), ("status", 1)],
            name="bookings_availability_status",
        )
    except PyMongoError:
        logger.warning("Could not create mentorship_bookings indexes.")


def _clean_booking(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    item = dict(doc)
    item["id"] = str(item["_id"])
    item["status_label"] = STATUS_LABELS.get(item.get("status", ""), item.get("status", ""))
    return item


def get_booking_by_id(booking_id: Any) -> dict[str, Any] | None:
    try:
        bid = parse_object_id(booking_id)
        doc = bookings_collection.find_one({"_id": bid})
        return _clean_booking(doc)
    except Exception:
        return None


def create_booking_request(
    *,
    student_user: dict[str, Any],
    mentor_id: Any,
    availability_id: Any,
    learning_objective: str,
    problem_description: str,
) -> ObjectId:
    if not isinstance(student_user, dict):
        raise BookingPermissionError("Authentication required.")

    student_id = parse_object_id(student_user.get("_id"))
    m_id = parse_object_id(mentor_id)
    slot_id = parse_object_id(availability_id)

    if student_id == m_id:
        raise BookingValidationError("You cannot book a mentorship session with yourself.")

    # Validate availability slot
    slot = repositories.get_availability_slot(slot_id)
    if not slot or not slot.get("is_active", False):
        raise BookingValidationError("The selected time slot is no longer active.")

    if slot.get("mentor_id") != m_id:
        raise BookingValidationError("The time slot does not belong to the selected mentor.")

    if slot.get("status") != "available" or slot.get("is_booked", False):
        raise SlotConflictError("This time slot is already booked.")

    # Check date isn't in the past
    slot_date_str = slot.get("date", "")
    try:
        slot_date = datetime.strptime(slot_date_str, "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        if slot_date < today:
            raise BookingValidationError("Cannot book an availability slot in the past.")
    except ValueError:
        pass

    # Check student doesn't have an active request for this same slot
    existing = bookings_collection.find_one({
        "student_id": student_id,
        "availability_id": slot_id,
        "status": {"$in": [STATUS_PENDING, STATUS_ACCEPTED, STATUS_RESCHEDULED_PENDING]},
    })
    if existing:
        raise BookingValidationError("You already have an active request or booking for this slot.")

    # Validate texts
    clean_objective = (learning_objective or "").strip()
    clean_problem = (problem_description or "").strip()

    if len(clean_objective) < 10 or len(clean_objective) > 500:
        raise BookingValidationError("Learning objective must be between 10 and 500 characters.")
    if len(clean_problem) < 10 or len(clean_problem) > 1000:
        raise BookingValidationError("Challenge description must be between 10 and 1000 characters.")

    mentor_user = db["users"].find_one({"_id": m_id})
    mentor_name = f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() if mentor_user else "Mentor"
    student_name = f"{student_user.get('first_name', '')} {student_user.get('last_name', '')}".strip() or "Student"
    student_email = student_user.get("email", "")

    now = _now()
    booking_doc: dict[str, Any] = {
        "_id": ObjectId(),
        "student_id": student_id,
        "student_name": student_name,
        "student_email": student_email,
        "mentor_id": m_id,
        "mentor_name": mentor_name,
        "mentor_email": mentor_user.get("email", "") if mentor_user else "",
        "availability_id": slot_id,
        "date": slot["date"],
        "start_time": slot["start_time"],
        "end_time": slot["end_time"],
        "duration_minutes": slot.get("duration_minutes", 60),
        "topic": slot.get("custom_topic") or slot.get("topic", "Mentoring"),
        "session_type": slot.get("session_type", "online"),
        "location": slot.get("location", ""),
        "meeting_link": "",
        "learning_objective": clean_objective,
        "problem_description": clean_problem,
        "status": STATUS_PENDING,
        "created_at": now,
        "updated_at": now,
        "decision_at": None,
        "rejection_reason": "",
        "cancellation_reason": "",
        "cancelled_by": "",
    }

    try:
        bookings_collection.insert_one(booking_doc)
    except PyMongoError as err:
        logger.error("Failed to insert booking: %s", err)
        raise BookingError("Database error while creating booking request.") from err

    # Create in-app notification for mentor
    notifications.create_notification(
        recipient_id=m_id,
        event_type="booking_request_submitted",
        title="New Mentorship Request",
        message=f"{student_name} requested a coaching session on {slot['date']} at {slot['start_time']}.",
        link_url="/users/backoffice/mentors/sessions/",
        related_booking_id=booking_doc["_id"],
    )

    # Send email notification to mentor
    if mentor_user and mentor_user.get("email"):
        email_service.send_templated_email(
            subject=f"New mentorship request — {student_name}",
            recipient_email=mentor_user["email"],
            template_name_base="booking_request_mentor",
            context={
                "mentor_name": mentor_name,
                "student_name": student_name,
                "topic": booking_doc["topic"],
                "date": booking_doc["date"],
                "start_time": booking_doc["start_time"],
                "end_time": booking_doc["end_time"],
                "duration_minutes": booking_doc["duration_minutes"],
                "session_type": booking_doc["session_type"],
                "learning_objective": clean_objective,
                "problem_description": clean_problem,
                "action_url": "/users/backoffice/mentors/sessions/",
            },
        )

    return booking_doc["_id"]


def mentor_accept_booking(
    *,
    booking_id: Any,
    mentor_user: dict[str, Any],
    meeting_link: str = "",
) -> bool:
    if not isinstance(mentor_user, dict):
        raise BookingPermissionError("Authentication required.")
    mid = parse_object_id(mentor_user.get("_id"))
    bid = parse_object_id(booking_id)

    booking = bookings_collection.find_one({"_id": bid, "mentor_id": mid})
    if not booking:
        raise BookingNotFoundError("Booking request not found.")

    if booking.get("status") not in (STATUS_PENDING, STATUS_RESCHEDULED_PENDING):
        raise BookingValidationError(f"Cannot accept request with status '{booking.get('status')}'.")

    slot_id = booking.get("availability_id")
    now = _now()

    # Atomically claim the slot
    slot_update = repositories.availability_collection.update_one(
        {
            "_id": slot_id,
            "mentor_id": mid,
            "status": "available",
            "is_booked": False,
        },
        {
            "$set": {
                "status": "booked",
                "is_booked": True,
                "booked_by": booking.get("student_id"),
                "updated_at": now,
            }
        },
    )

    if slot_update.matched_count == 0:
        # Check if it was already booked by this exact booking (idempotency)
        slot = repositories.availability_collection.find_one({"_id": slot_id})
        if not slot or slot.get("booked_by") != booking.get("student_id"):
            raise SlotConflictError("This slot has already been booked by another session.")

    clean_link = (meeting_link or "").strip()
    if clean_link and not (clean_link.startswith("http://") or clean_link.startswith("https://")):
        clean_link = f"https://{clean_link}"

    update_fields: dict[str, Any] = {
        "status": STATUS_ACCEPTED,
        "decision_at": now,
        "updated_at": now,
    }
    if clean_link:
        update_fields["meeting_link"] = clean_link

    bookings_collection.update_one({"_id": bid}, {"$set": update_fields})

    # Notify student
    student_id = booking.get("student_id")
    mentor_name = f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Mentor"
    notifications.create_notification(
        recipient_id=student_id,
        event_type="booking_accepted",
        title="Session Confirmed!",
        message=f"{mentor_name} has accepted your mentorship session on {booking['date']} at {booking['start_time']}.",
        link_url="/users/profile/",
        related_booking_id=bid,
    )

    # Email student
    student_user = db["users"].find_one({"_id": student_id})
    if student_user and student_user.get("email"):
        email_service.send_templated_email(
            subject=f"Session Confirmed — Mentorship with {mentor_name}",
            recipient_email=student_user["email"],
            template_name_base="booking_accepted_student",
            context={
                "student_name": f"{student_user.get('first_name', '')}".strip() or "Student",
                "mentor_name": mentor_name,
                "topic": booking.get("topic", "Mentoring"),
                "date": booking.get("date"),
                "start_time": booking.get("start_time"),
                "end_time": booking.get("end_time"),
                "duration_minutes": booking.get("duration_minutes", 60),
                "session_type": booking.get("session_type", "online"),
                "meeting_link": clean_link or booking.get("meeting_link"),
                "location": booking.get("location"),
                "action_url": "/users/profile/",
            },
        )

    return True


def mentor_reject_booking(
    *,
    booking_id: Any,
    mentor_user: dict[str, Any],
    rejection_reason: str = "",
) -> bool:
    if not isinstance(mentor_user, dict):
        raise BookingPermissionError("Authentication required.")
    mid = parse_object_id(mentor_user.get("_id"))
    bid = parse_object_id(booking_id)

    booking = bookings_collection.find_one({"_id": bid, "mentor_id": mid})
    if not booking:
        raise BookingNotFoundError("Booking request not found.")

    if booking.get("status") not in (STATUS_PENDING, STATUS_RESCHEDULED_PENDING):
        raise BookingValidationError(f"Cannot reject request with status '{booking.get('status')}'.")

    now = _now()
    clean_reason = (rejection_reason or "").strip()[:500]

    bookings_collection.update_one(
        {"_id": bid},
        {
            "$set": {
                "status": STATUS_REJECTED,
                "rejection_reason": clean_reason,
                "decision_at": now,
                "updated_at": now,
            }
        },
    )

    # Release slot if provisionally marked
    slot_id = booking.get("availability_id")
    repositories.availability_collection.update_one(
        {"_id": slot_id, "mentor_id": mid, "booked_by": booking.get("student_id")},
        {"$set": {"status": "available", "is_booked": False, "booked_by": None, "updated_at": now}},
    )

    # In-app notification to student
    student_id = booking.get("student_id")
    mentor_name = f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Mentor"
    notifications.create_notification(
        recipient_id=student_id,
        event_type="booking_rejected",
        title="Mentorship Request Declined",
        message=f"{mentor_name} was unable to accept your request for {booking['date']}.",
        link_url="/users/mentors/",
        related_booking_id=bid,
    )

    # Email student
    student_user = db["users"].find_one({"_id": student_id})
    if student_user and student_user.get("email"):
        email_service.send_templated_email(
            subject=f"Mentorship Request Update — {mentor_name}",
            recipient_email=student_user["email"],
            template_name_base="booking_rejected_student",
            context={
                "student_name": f"{student_user.get('first_name', '')}".strip() or "Student",
                "mentor_name": mentor_name,
                "date": booking.get("date"),
                "start_time": booking.get("start_time"),
                "reason": clean_reason,
                "action_url": "/users/mentors/",
            },
        )

    return True


def mentor_propose_reschedule(
    *,
    booking_id: Any,
    mentor_user: dict[str, Any],
    proposed_date: str,
    proposed_start_time: str,
    proposed_end_time: str,
    reschedule_note: str = "",
) -> bool:
    if not isinstance(mentor_user, dict):
        raise BookingPermissionError("Authentication required.")
    mid = parse_object_id(mentor_user.get("_id"))
    bid = parse_object_id(booking_id)

    booking = bookings_collection.find_one({"_id": bid, "mentor_id": mid})
    if not booking:
        raise BookingNotFoundError("Booking request not found.")

    if booking.get("status") not in (STATUS_PENDING, STATUS_ACCEPTED, STATUS_RESCHEDULED_PENDING):
        raise BookingValidationError("Can only propose reschedule for pending, accepted, or rescheduled bookings.")

    parsed_date = parse_date_value(proposed_date)
    today = current_local_date()
    if parsed_date < today:
        raise BookingValidationError("Proposed date cannot be in the past. Please select today or a future date.")

    original_date_str = booking.get("date")
    if original_date_str:
        original_date = None
        try:
            original_date = parse_date_value(original_date_str)
        except Exception:
            original_date = None
        if original_date and parsed_date < original_date:
            raise BookingValidationError(
                f"Proposed date cannot be before the original scheduled date ({original_date_str})."
            )

    parsed_start = parse_time_value(
        proposed_start_time,
        field_name="proposed_start_time",
        missing_message="Proposed start time is required.",
    )
    parsed_end = parse_time_value(
        proposed_end_time,
        field_name="proposed_end_time",
        missing_message="Proposed end time is required.",
    )

    now_local = current_local_datetime()
    if parsed_date == now_local.date() and parsed_start <= now_local.time():
        raise BookingValidationError("Proposed start time has already passed for today. Please select a future time.")

    duration_minutes = calculate_duration_minutes(parsed_start, parsed_end)
    if duration_minutes <= 0:
        raise BookingValidationError("Proposed end time must be later than proposed start time.")
    if duration_minutes < MIN_DURATION_MINUTES:
        raise BookingValidationError(f"Proposed session must last at least {MIN_DURATION_MINUTES} minutes.")

    now = _now()
    bookings_collection.update_one(
        {"_id": bid},
        {
            "$set": {
                "status": STATUS_RESCHEDULED_PENDING,
                "proposed_date": proposed_date,
                "proposed_start_time": proposed_start_time,
                "proposed_end_time": proposed_end_time,
                "reschedule_note": (reschedule_note or "").strip()[:500],
                "updated_at": now,
            }
        },
    )

    student_id = booking.get("student_id")
    mentor_name = f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Mentor"
    notifications.create_notification(
        recipient_id=student_id,
        event_type="proposal_received",
        title="Reschedule Proposal Received",
        message=f"{mentor_name} proposed a new time: {proposed_date} at {proposed_start_time}.",
        link_url="/users/profile/",
        related_booking_id=bid,
    )

    student_user = db["users"].find_one({"_id": student_id})
    if student_user and student_user.get("email"):
        email_service.send_templated_email(
            subject=f"New Time Proposed — Mentorship with {mentor_name}",
            recipient_email=student_user["email"],
            template_name_base="reschedule_proposal_student",
            context={
                "student_name": f"{student_user.get('first_name', '')}".strip() or "Student",
                "mentor_name": mentor_name,
                "original_date": booking.get("date"),
                "original_start_time": booking.get("start_time"),
                "proposed_date": proposed_date,
                "proposed_start_time": proposed_start_time,
                "proposed_end_time": proposed_end_time,
                "note": (reschedule_note or "").strip(),
                "action_url": "/users/profile/",
            },
        )

    return True


def student_respond_reschedule(
    *,
    booking_id: Any,
    student_user: dict[str, Any],
    accept: bool,
) -> bool:
    if not isinstance(student_user, dict):
        raise BookingPermissionError("Authentication required.")
    sid = parse_object_id(student_user.get("_id"))
    bid = parse_object_id(booking_id)

    booking = bookings_collection.find_one({"_id": bid, "student_id": sid})
    if not booking:
        raise BookingNotFoundError("Booking request not found.")

    if booking.get("status") != STATUS_RESCHEDULED_PENDING:
        raise BookingValidationError("No pending reschedule proposal found for this booking.")

    now = _now()
    student_name = f"{student_user.get('first_name', '')} {student_user.get('last_name', '')}".strip() or "Student"
    mentor_id = booking.get("mentor_id")

    if accept:
        new_date = booking.get("proposed_date", booking["date"])
        new_start = booking.get("proposed_start_time", booking["start_time"])
        new_end = booking.get("proposed_end_time", booking["end_time"])

        bookings_collection.update_one(
            {"_id": bid},
            {
                "$set": {
                    "status": STATUS_ACCEPTED,
                    "date": new_date,
                    "start_time": new_start,
                    "end_time": new_end,
                    "updated_at": now,
                }
            },
        )
        notifications.create_notification(
            recipient_id=mentor_id,
            event_type="proposal_accepted",
            title="Reschedule Proposal Accepted",
            message=f"{student_name} accepted your proposed time: {new_date} at {new_start}.",
            link_url="/users/backoffice/mentors/sessions/",
            related_booking_id=bid,
        )
    else:
        bookings_collection.update_one(
            {"_id": bid},
            {
                "$set": {
                    "status": STATUS_CANCELLED,
                    "cancelled_by": "student",
                    "cancellation_reason": "Declined reschedule proposal.",
                    "updated_at": now,
                }
            },
        )
        # Release slot
        slot_id = booking.get("availability_id")
        repositories.availability_collection.update_one(
            {"_id": slot_id},
            {"$set": {"status": "available", "is_booked": False, "booked_by": None, "updated_at": now}},
        )
        notifications.create_notification(
            recipient_id=mentor_id,
            event_type="proposal_declined",
            title="Reschedule Proposal Declined",
            message=f"{student_name} declined the proposed time. The session has been cancelled.",
            link_url="/users/backoffice/mentors/sessions/",
            related_booking_id=bid,
        )

    return True


def student_cancel_booking(
    *,
    booking_id: Any,
    student_user: dict[str, Any],
    cancellation_reason: str = "",
) -> bool:
    if not isinstance(student_user, dict):
        raise BookingPermissionError("Authentication required.")
    sid = parse_object_id(student_user.get("_id"))
    bid = parse_object_id(booking_id)

    booking = bookings_collection.find_one({"_id": bid, "student_id": sid})
    if not booking:
        raise BookingNotFoundError("Booking request not found.")

    if booking.get("status") not in (STATUS_PENDING, STATUS_ACCEPTED, STATUS_RESCHEDULED_PENDING):
        raise BookingValidationError("This booking can no longer be cancelled.")

    now = _now()
    clean_reason = (cancellation_reason or "").strip()[:500]

    bookings_collection.update_one(
        {"_id": bid},
        {
            "$set": {
                "status": STATUS_CANCELLED,
                "cancelled_by": "student",
                "cancellation_reason": clean_reason,
                "updated_at": now,
            }
        },
    )

    # Release availability slot
    slot_id = booking.get("availability_id")
    repositories.availability_collection.update_one(
        {"_id": slot_id},
        {"$set": {"status": "available", "is_booked": False, "booked_by": None, "updated_at": now}},
    )

    mentor_id = booking.get("mentor_id")
    student_name = f"{student_user.get('first_name', '')} {student_user.get('last_name', '')}".strip() or "Student"
    notifications.create_notification(
        recipient_id=mentor_id,
        event_type="booking_cancelled",
        title="Session Cancelled by Student",
        message=f"{student_name} cancelled the session on {booking['date']} at {booking['start_time']}.",
        link_url="/users/backoffice/mentors/sessions/",
        related_booking_id=bid,
    )

    mentor_user = db["users"].find_one({"_id": mentor_id})
    if mentor_user and mentor_user.get("email"):
        email_service.send_templated_email(
            subject=f"Session Cancelled — {student_name}",
            recipient_email=mentor_user["email"],
            template_name_base="booking_cancelled",
            context={
                "recipient_name": f"{mentor_user.get('first_name', '')}".strip() or "Mentor",
                "other_party_name": student_name,
                "date": booking.get("date"),
                "start_time": booking.get("start_time"),
                "cancelled_by": "the student",
                "reason": clean_reason,
                "action_url": "/users/backoffice/mentors/sessions/",
            },
        )

    return True


def mentor_cancel_booking(
    *,
    booking_id: Any,
    mentor_user: dict[str, Any],
    cancellation_reason: str = "",
) -> bool:
    if not isinstance(mentor_user, dict):
        raise BookingPermissionError("Authentication required.")
    mid = parse_object_id(mentor_user.get("_id"))
    bid = parse_object_id(booking_id)

    booking = bookings_collection.find_one({"_id": bid, "mentor_id": mid})
    if not booking:
        raise BookingNotFoundError("Booking request not found.")

    if booking.get("status") not in (STATUS_PENDING, STATUS_ACCEPTED, STATUS_RESCHEDULED_PENDING):
        raise BookingValidationError("This booking can no longer be cancelled.")

    now = _now()
    clean_reason = (cancellation_reason or "").strip()[:500]

    bookings_collection.update_one(
        {"_id": bid},
        {
            "$set": {
                "status": STATUS_CANCELLED,
                "cancelled_by": "mentor",
                "cancellation_reason": clean_reason,
                "updated_at": now,
            }
        },
    )

    slot_id = booking.get("availability_id")
    repositories.availability_collection.update_one(
        {"_id": slot_id, "mentor_id": mid},
        {"$set": {"status": "available", "is_booked": False, "booked_by": None, "updated_at": now}},
    )

    student_id = booking.get("student_id")
    mentor_name = f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Mentor"
    notifications.create_notification(
        recipient_id=student_id,
        event_type="booking_cancelled",
        title="Session Cancelled by Mentor",
        message=f"{mentor_name} had to cancel the session on {booking['date']}.",
        link_url="/users/profile/",
        related_booking_id=bid,
    )

    student_user = db["users"].find_one({"_id": student_id})
    if student_user and student_user.get("email"):
        email_service.send_templated_email(
            subject=f"Session Cancelled — {mentor_name}",
            recipient_email=student_user["email"],
            template_name_base="booking_cancelled",
            context={
                "recipient_name": f"{student_user.get('first_name', '')}".strip() or "Student",
                "other_party_name": mentor_name,
                "date": booking.get("date"),
                "start_time": booking.get("start_time"),
                "cancelled_by": "your mentor",
                "reason": clean_reason,
                "action_url": "/users/profile/",
            },
        )

    return True


def mentor_update_meeting_link(
    *,
    booking_id: Any,
    mentor_user: dict[str, Any],
    meeting_link: str,
) -> bool:
    if not isinstance(mentor_user, dict):
        raise BookingPermissionError("Authentication required.")
    mid = parse_object_id(mentor_user.get("_id"))
    bid = parse_object_id(booking_id)

    booking = bookings_collection.find_one({"_id": bid, "mentor_id": mid})
    if not booking:
        raise BookingNotFoundError("Booking request not found.")

    if booking.get("status") != STATUS_ACCEPTED:
        raise BookingValidationError("Meeting link can only be set for accepted sessions.")

    clean_link = (meeting_link or "").strip()
    if clean_link and not (clean_link.startswith("http://") or clean_link.startswith("https://")):
        clean_link = f"https://{clean_link}"

    now = _now()
    bookings_collection.update_one(
        {"_id": bid},
        {"$set": {"meeting_link": clean_link, "updated_at": now}},
    )

    student_id = booking.get("student_id")
    mentor_name = f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Mentor"
    notifications.create_notification(
        recipient_id=student_id,
        event_type="meeting_link_updated",
        title="Meeting Link Added",
        message=f"{mentor_name} added the meeting link for your session on {booking['date']}.",
        link_url="/users/profile/",
        related_booking_id=bid,
    )

    student_user = db["users"].find_one({"_id": student_id})
    if student_user and student_user.get("email"):
        email_service.send_templated_email(
            subject=f"Meeting Link Available — Mentorship with {mentor_name}",
            recipient_email=student_user["email"],
            template_name_base="meeting_link_updated",
            context={
                "student_name": f"{student_user.get('first_name', '')}".strip() or "Student",
                "mentor_name": mentor_name,
                "topic": booking.get("topic", "Mentoring"),
                "date": booking.get("date"),
                "start_time": booking.get("start_time"),
                "end_time": booking.get("end_time"),
                "meeting_link": clean_link,
                "action_url": "/users/profile/",
            },
        )

    return True


def mentor_complete_session(
    *,
    booking_id: Any,
    mentor_user: dict[str, Any],
) -> bool:
    if not isinstance(mentor_user, dict):
        raise BookingPermissionError("Authentication required.")
    mid = parse_object_id(mentor_user.get("_id"))
    bid = parse_object_id(booking_id)

    booking = bookings_collection.find_one({"_id": bid, "mentor_id": mid})
    if not booking:
        raise BookingNotFoundError("Booking request not found.")

    if booking.get("status") != STATUS_ACCEPTED:
        raise BookingValidationError("Only confirmed sessions can be marked as completed.")

    now = _now()
    bookings_collection.update_one(
        {"_id": bid},
        {"$set": {"status": STATUS_COMPLETED, "completed_at": now, "updated_at": now}},
    )

    # Prompt student for verified review!
    student_id = booking.get("student_id")
    mentor_name = f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Mentor"
    notifications.create_notification(
        recipient_id=student_id,
        event_type="session_completed",
        title="Session Completed — Share Feedback",
        message=f"How was your session with {mentor_name}? Leave a verified review to help others.",
        link_url=f"/users/mentors/reviews/{bid}/add/",
        related_booking_id=bid,
    )

    student_user = db["users"].find_one({"_id": student_id})
    if student_user and student_user.get("email"):
        email_service.send_templated_email(
            subject=f"Session Completed — Leave a Review for {mentor_name}",
            recipient_email=student_user["email"],
            template_name_base="session_completed",
            context={
                "student_name": f"{student_user.get('first_name', '')}".strip() or "Student",
                "mentor_name": mentor_name,
                "topic": booking.get("topic", "Mentoring"),
                "action_url": f"/users/mentors/reviews/{bid}/add/",
            },
        )

    return True


def mentor_mark_no_show(
    *,
    booking_id: Any,
    mentor_user: dict[str, Any],
) -> bool:
    if not isinstance(mentor_user, dict):
        raise BookingPermissionError("Authentication required.")
    mid = parse_object_id(mentor_user.get("_id"))
    bid = parse_object_id(booking_id)

    booking = bookings_collection.find_one({"_id": bid, "mentor_id": mid})
    if not booking:
        raise BookingNotFoundError("Booking request not found.")

    if booking.get("status") != STATUS_ACCEPTED:
        raise BookingValidationError("Only confirmed sessions can be marked as no-show.")

    now = _now()
    bookings_collection.update_one(
        {"_id": bid},
        {"$set": {"status": STATUS_NO_SHOW, "updated_at": now}},
    )

    student_id = booking.get("student_id")
    mentor_name = f"{mentor_user.get('first_name', '')} {mentor_user.get('last_name', '')}".strip() or "Mentor"
    notifications.create_notification(
        recipient_id=student_id,
        event_type="session_no_show",
        title="Session Marked as No-Show",
        message=f"Your session with {mentor_name} on {booking['date']} was recorded as a no-show.",
        link_url="/users/profile/",
        related_booking_id=bid,
    )

    return True


def list_student_bookings(student_id: Any, status: str | None = None) -> list[dict[str, Any]]:
    try:
        sid = parse_object_id(student_id)
        query: dict[str, Any] = {"student_id": sid}
        if status:
            query["status"] = status
        cursor = bookings_collection.find(query).sort("created_at", -1)
        return [_clean_booking(item) for item in cursor if item]
    except Exception as err:
        logger.error("Failed to list student bookings for %s: %s", student_id, err)
        return []


def list_mentor_bookings(mentor_id: Any, status: str | None = None) -> list[dict[str, Any]]:
    try:
        mid = parse_object_id(mentor_id)
        query: dict[str, Any] = {"mentor_id": mid}
        if status:
            query["status"] = status
        cursor = bookings_collection.find(query).sort([("date", 1), ("start_time", 1)])
        return [_clean_booking(item) for item in cursor if item]
    except Exception as err:
        logger.error("Failed to list mentor bookings for %s: %s", mentor_id, err)
        return []


def get_mentor_session_stats(mentor_id: Any) -> dict[str, int]:
    try:
        mid = parse_object_id(mentor_id)
        pending = bookings_collection.count_documents({
            "mentor_id": mid,
            "status": {"$in": [STATUS_PENDING, STATUS_RESCHEDULED_PENDING]},
        })
        upcoming = bookings_collection.count_documents({
            "mentor_id": mid,
            "status": STATUS_ACCEPTED,
        })
        completed = bookings_collection.count_documents({
            "mentor_id": mid,
            "status": STATUS_COMPLETED,
        })
        cancellations = bookings_collection.count_documents({
            "mentor_id": mid,
            "status": {"$in": [STATUS_CANCELLED, STATUS_REJECTED, STATUS_NO_SHOW]},
        })
        return {
            "pending_count": pending,
            "upcoming_count": upcoming,
            "completed_count": completed,
            "cancellations_count": cancellations,
        }
    except Exception:
        return {
            "pending_count": 0,
            "upcoming_count": 0,
            "completed_count": 0,
            "cancellations_count": 0,
        }


def get_student_session_stats(student_id: Any) -> dict[str, int]:
    try:
        sid = parse_object_id(student_id)
        upcoming = bookings_collection.count_documents({
            "student_id": sid,
            "status": STATUS_ACCEPTED,
        })
        pending = bookings_collection.count_documents({
            "student_id": sid,
            "status": {"$in": [STATUS_PENDING, STATUS_RESCHEDULED_PENDING]},
        })
        completed = bookings_collection.count_documents({
            "student_id": sid,
            "status": STATUS_COMPLETED,
        })
        return {
            "upcoming_count": upcoming,
            "pending_count": pending,
            "completed_count": completed,
        }
    except Exception:
        return {
            "upcoming_count": 0,
            "pending_count": 0,
            "completed_count": 0,
        }
