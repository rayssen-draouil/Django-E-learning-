from __future__ import annotations

from typing import Any

from bson import ObjectId
from pymongo.collection import Collection

from config.mongodb import db

learning_paths_collection: Collection[dict[str, Any]] = db["learning_paths"]
enrollments_collection: Collection[dict[str, Any]] = db["learning_path_enrollments"]


def list_learning_paths(*, created_by: ObjectId | None = None, published_only: bool = False) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if created_by is not None:
        query["created_by"] = created_by
    if published_only:
        query["status"] = "published"
    return list(learning_paths_collection.find(query).sort("updated_at", -1))


def get_learning_path(path_id: ObjectId) -> dict[str, Any] | None:
    return learning_paths_collection.find_one({"_id": path_id})


def insert_learning_path(document: dict[str, Any]) -> ObjectId:
    return learning_paths_collection.insert_one(document).inserted_id


def update_learning_path(path_id: ObjectId, fields: dict[str, Any]) -> bool:
    result = learning_paths_collection.update_one({"_id": path_id}, {"$set": fields})
    return result.matched_count == 1


def delete_learning_path(path_id: ObjectId) -> bool:
    deleted = learning_paths_collection.delete_one({"_id": path_id}).deleted_count == 1
    if deleted:
        enrollments_collection.delete_many({"learning_path_id": path_id})
    return deleted


def enroll_student(learning_path_id: ObjectId, user_id: ObjectId, timestamp: Any) -> bool:
    result = enrollments_collection.update_one(
        {"learning_path_id": learning_path_id, "user_id": user_id},
        {"$setOnInsert": {
            "learning_path_id": learning_path_id,
            "user_id": user_id,
            "completed_course_ids": [],
            "enrolled_at": timestamp,
            "updated_at": timestamp,
        }},
        upsert=True,
    )
    return result.upserted_id is not None


def get_enrollment(learning_path_id: ObjectId, user_id: ObjectId) -> dict[str, Any] | None:
    return enrollments_collection.find_one({
        "learning_path_id": learning_path_id,
        "user_id": user_id,
    })


def list_student_enrollments(user_id: ObjectId) -> list[dict[str, Any]]:
    return list(enrollments_collection.find({"user_id": user_id}).sort("updated_at", -1))


def set_course_completion(
    learning_path_id: ObjectId,
    user_id: ObjectId,
    course_id: str,
    completed: bool,
    timestamp: Any,
) -> bool:
    update = {"$addToSet": {"completed_course_ids": course_id}} if completed else {
        "$pull": {"completed_course_ids": course_id}
    }
    update["$set"] = {"updated_at": timestamp}
    result = enrollments_collection.update_one(
        {"learning_path_id": learning_path_id, "user_id": user_id},
        update,
    )
    return result.matched_count == 1


def list_path_enrollments(learning_path_id: ObjectId) -> list[dict[str, Any]]:
    return list(enrollments_collection.find(
        {"learning_path_id": learning_path_id}
    ).sort("enrolled_at", 1))


def list_enrollments_for_paths(learning_path_ids: list[ObjectId]) -> list[dict[str, Any]]:
    if not learning_path_ids:
        return []
    return list(enrollments_collection.find({
        "learning_path_id": {"$in": learning_path_ids},
    }))


def count_enrollments(learning_path_ids: list[ObjectId]) -> dict[ObjectId, int]:
    if not learning_path_ids:
        return {}
    return {
        item["_id"]: item["count"]
        for item in enrollments_collection.aggregate([
            {"$match": {"learning_path_id": {"$in": learning_path_ids}}},
            {"$group": {"_id": "$learning_path_id", "count": {"$sum": 1}}},
        ])
    }


def create_indexes() -> None:
    learning_paths_collection.create_index(
        [("status", 1), ("updated_at", -1)],
        name="learning_paths_status_updated",
    )
    learning_paths_collection.create_index(
        [("created_by", 1), ("updated_at", -1)],
        name="learning_paths_author_updated",
    )
    enrollments_collection.create_index(
        [("learning_path_id", 1), ("user_id", 1)],
        unique=True,
        name="learning_path_enrollments_unique",
    )
    enrollments_collection.create_index(
        [("user_id", 1), ("updated_at", -1)],
        name="learning_path_enrollments_student_updated",
    )
