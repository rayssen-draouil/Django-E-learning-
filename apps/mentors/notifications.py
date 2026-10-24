from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from config.mongodb import db

logger = logging.getLogger(__name__)

notifications_collection: Collection[dict[str, Any]] = db["notifications"]

EVENT_TYPES = {
    "booking_request_submitted",
    "booking_accepted",
    "booking_rejected",
    "booking_cancelled",
    "proposal_received",
    "proposal_accepted",
    "proposal_declined",
    "meeting_link_updated",
    "session_reminder",
    "session_completed",
    "session_no_show",
    "review_received",
    "review_response_added",
    "feedback_reminder",
    "recommendation_booking_submitted",
    "classroom_invitation_sent",
    "classroom_invitation_failed",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_object_id(value: Any) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    raise ValueError(f"Invalid ObjectId: {value}")


def create_indexes() -> None:
    try:
        notifications_collection.create_index(
            [("recipient_id", 1), ("is_read", 1), ("created_at", -1)],
            name="notifications_recipient_unread",
        )
        notifications_collection.create_index(
            [("recipient_id", 1), ("created_at", -1)],
            name="notifications_recipient_recent",
        )
    except PyMongoError:
        logger.warning("Could not create notification indexes.")


def create_notification(
    *,
    recipient_id: Any,
    event_type: str,
    title: str,
    message: str,
    link_url: str = "",
    related_booking_id: Any = None,
    metadata: dict[str, Any] | None = None,
) -> ObjectId | None:
    try:
        rec_id = parse_object_id(recipient_id)
        book_id = parse_object_id(related_booking_id) if related_booking_id else None
        now = _now()
        doc: dict[str, Any] = {
            "_id": ObjectId(),
            "recipient_id": rec_id,
            "event_type": event_type,
            "title": title.strip(),
            "message": message.strip(),
            "link_url": link_url.strip(),
            "related_booking_id": book_id,
            "metadata": metadata or {},
            "is_read": False,
            "created_at": now,
            "read_at": None,
        }
        notifications_collection.insert_one(doc)
        return doc["_id"]
    except Exception as error:
        logger.error("Failed to create notification for %s: %s", recipient_id, error)
        return None


def list_user_notifications(
    user_id: Any,
    *,
    limit: int = 40,
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    try:
        uid = parse_object_id(user_id)
        query: dict[str, Any] = {"recipient_id": uid}
        if unread_only:
            query["is_read"] = False
        cursor = notifications_collection.find(query).sort("created_at", -1).limit(limit)
        items = []
        for doc in cursor:
            item = dict(doc)
            item["id"] = str(item["_id"])
            items.append(item)
        return items
    except Exception as error:
        logger.error("Failed to list notifications for user %s: %s", user_id, error)
        return []


def get_unread_notification_count(user_id: Any) -> int:
    try:
        uid = parse_object_id(user_id)
        return notifications_collection.count_documents(
            {"recipient_id": uid, "is_read": False}
        )
    except Exception:
        return 0


get_unread_count = get_unread_notification_count


def mark_notification_as_read(
    notification_id: Any,
    user_id: Any = None,
    *,
    recipient_id: Any = None,
) -> bool:
    target_user_id = recipient_id if recipient_id is not None else user_id
    if target_user_id is None:
        raise ValueError("recipient_id or user_id must be provided")
    try:
        nid = parse_object_id(notification_id)
        uid = parse_object_id(target_user_id)
        result = notifications_collection.update_one(
            {"_id": nid, "recipient_id": uid},
            {"$set": {"is_read": True, "read_at": _now()}},
        )
        return result.matched_count == 1
    except Exception as error:
        logger.error("Failed to mark notification %s as read: %s", notification_id, error)
        return False


def mark_all_notifications_as_read(user_id: Any = None, *, recipient_id: Any = None) -> int:
    target_user_id = recipient_id if recipient_id is not None else user_id
    if target_user_id is None:
        raise ValueError("recipient_id or user_id must be provided")
    try:
        uid = parse_object_id(target_user_id)
        result = notifications_collection.update_many(
            {"recipient_id": uid, "is_read": False},
            {"$set": {"is_read": True, "read_at": _now()}},
        )
        return result.modified_count
    except Exception as error:
        logger.error("Failed to mark all notifications read for user %s: %s", user_id, error)
        return 0
