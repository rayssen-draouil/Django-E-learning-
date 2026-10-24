from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId
from pymongo.collection import Collection

from config.mongodb import db

availability_collection: Collection[dict[str, Any]] = db["mentor_availability"]

ACTIVE_STATUSES = ("available", "booked")


def list_mentor_availability(
    mentor_id: ObjectId,
    *,
    date: str | None = None,
    topic: str | None = None,
    session_type: str | None = None,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"mentor_id": mentor_id}
    if not include_inactive:
        query["status"] = {"$in": list(ACTIVE_STATUSES)}
        query["is_active"] = {"$ne": False}
    if date:
        query["date"] = date
    if topic:
        query["topic"] = topic
    if session_type:
        query["session_type"] = session_type
    return list(
        availability_collection.find(query).sort([("date", 1), ("start_time", 1)])
    )


def get_availability_slot(
    slot_id: ObjectId,
    *,
    include_inactive: bool = False,
) -> dict[str, Any] | None:
    query: dict[str, Any] = {"_id": slot_id}
    if not include_inactive:
        query["status"] = {"$in": list(ACTIVE_STATUSES)}
        query["is_active"] = {"$ne": False}
    return availability_collection.find_one(query)


def find_overlapping_slots(
    mentor_id: ObjectId,
    *,
    date: str,
    start_time: str,
    end_time: str,
    exclude_slot_id: ObjectId | None = None,
) -> list[dict[str, Any]]:
    """
    Find active availability slots for the mentor on the given date that overlap
    with [start_time, end_time) using the standard interval overlap condition:
        existing_start < new_end AND existing_end > new_start
    Adjacent intervals (e.g. 10:00-11:00 and 11:00-12:00) do not overlap.
    """
    query: dict[str, Any] = {
        "mentor_id": mentor_id,
        "date": date,
        "status": {"$in": list(ACTIVE_STATUSES)},
        "is_active": {"$ne": False},
        "start_time": {"$lt": end_time},
        "end_time": {"$gt": start_time},
    }
    if exclude_slot_id is not None:
        query["_id"] = {"$ne": exclude_slot_id}
    return list(availability_collection.find(query).sort("start_time", 1))


def insert_availability(document: dict[str, Any]) -> ObjectId:
    return availability_collection.insert_one(document).inserted_id


def update_availability(
    slot_id: ObjectId,
    mentor_id: ObjectId,
    fields: dict[str, Any],
) -> bool:
    result = availability_collection.update_one(
        {
            "_id": slot_id,
            "mentor_id": mentor_id,
            "status": {"$in": list(ACTIVE_STATUSES)},
            "is_active": {"$ne": False},
        },
        {"$set": fields},
    )
    return result.matched_count == 1


def deactivate_availability(
    slot_id: ObjectId,
    mentor_id: ObjectId,
    updated_at: datetime,
) -> bool:
    result = availability_collection.update_one(
        {
            "_id": slot_id,
            "mentor_id": mentor_id,
            "status": "available",
            "is_active": {"$ne": False},
        },
        {
            "$set": {
                "status": "inactive",
                "is_active": False,
                "deleted_at": updated_at,
                "updated_at": updated_at,
            }
        },
    )
    return result.matched_count == 1


def delete_availability(slot_id: ObjectId, mentor_id: ObjectId) -> bool:
    result = availability_collection.delete_one(
        {"_id": slot_id, "mentor_id": mentor_id}
    )
    return result.deleted_count == 1


def create_indexes() -> None:
    availability_collection.create_index(
        [("mentor_id", 1), ("date", 1), ("start_time", 1)],
        name="mentor_availability_owner_schedule",
    )
    availability_collection.create_index(
        [("mentor_id", 1), ("status", 1), ("date", 1)],
        name="mentor_availability_owner_status_date",
    )
    availability_collection.create_index(
        [("status", 1), ("date", 1), ("topic", 1), ("session_type", 1)],
        name="mentor_availability_discovery",
    )
