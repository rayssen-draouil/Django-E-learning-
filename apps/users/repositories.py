from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo.collection import Collection

from config.mongodb import db

users_collection: Collection[dict[str, Any]] = db["users"]


def insert_user(document: dict[str, Any]) -> None:
    users_collection.insert_one(document)


def find_active_user(email: str) -> dict[str, Any] | None:
    return users_collection.find_one(
        {"email": email, "is_active": True},
        {"password": 1, "first_name": 1, "last_name": 1, "email": 1,
         "role": 1, "is_active": 1, "created_at": 1, "updated_at": 1},
    )


def find_user(user_id: Any) -> dict[str, Any] | None:
    return users_collection.find_one({"_id": user_id, "is_active": True}, {"password": 0})


def find_active_users(user_ids: list[Any]) -> list[dict[str, Any]]:
    return list(users_collection.find(
        {"_id": {"$in": user_ids}, "is_active": True},
        {"first_name": 1, "last_name": 1, "email": 1},
    ))


def update_user(user_id: Any, first_name: str, last_name: str, updated_at: datetime) -> bool:
    result = users_collection.update_one(
        {"_id": user_id, "is_active": True},
        {"$set": {"first_name": first_name, "last_name": last_name, "updated_at": updated_at}},
    )
    return result.matched_count == 1


def create_email_index() -> None:
    users_collection.create_index("email", unique=True, name="users_email_unique")
