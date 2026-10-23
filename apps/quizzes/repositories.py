from __future__ import annotations

from typing import Any

from bson import ObjectId
from pymongo.collection import Collection

from config.mongodb import db

quizzes_collection: Collection[dict[str, Any]] = db["quizzes"]
attempts_collection: Collection[dict[str, Any]] = db["quiz_attempts"]


def list_quizzes(*, created_by: ObjectId | None = None, published_only: bool = False) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if created_by is not None:
        query["created_by"] = created_by
    if published_only:
        query["status"] = "published"
    return list(quizzes_collection.find(query).sort("updated_at", -1))


def get_quiz(quiz_id: ObjectId) -> dict[str, Any] | None:
    return quizzes_collection.find_one({"_id": quiz_id})


def insert_quiz(document: dict[str, Any]) -> ObjectId:
    return quizzes_collection.insert_one(document).inserted_id


def update_quiz(quiz_id: ObjectId, fields: dict[str, Any]) -> bool:
    result = quizzes_collection.update_one({"_id": quiz_id}, {"$set": fields})
    return result.matched_count == 1


def delete_quiz(quiz_id: ObjectId) -> bool:
    return quizzes_collection.delete_one({"_id": quiz_id}).deleted_count == 1


def insert_attempt(document: dict[str, Any]) -> ObjectId:
    return attempts_collection.insert_one(document).inserted_id


def list_student_attempts(user_id: ObjectId, *, limit: int | None = None) -> list[dict[str, Any]]:
    cursor = attempts_collection.find({"user_id": user_id}).sort("submitted_at", -1)
    if limit is not None:
        cursor = cursor.limit(limit)
    return list(cursor)


def get_student_attempt(attempt_id: ObjectId, user_id: ObjectId) -> dict[str, Any] | None:
    return attempts_collection.find_one({"_id": attempt_id, "user_id": user_id})


def create_indexes() -> None:
    quizzes_collection.create_index([("created_by", 1), ("updated_at", -1)], name="quizzes_author_updated")
    quizzes_collection.create_index([("status", 1), ("updated_at", -1)], name="quizzes_status_updated")
    attempts_collection.create_index([("user_id", 1), ("submitted_at", -1)], name="quiz_attempts_student_recent")
    attempts_collection.create_index([("quiz_id", 1), ("user_id", 1)], name="quiz_attempts_quiz_student")
