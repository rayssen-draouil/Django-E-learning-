from __future__ import annotations

from datetime import datetime, timezone
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


def find_user_for_password_check(user_id: Any) -> dict[str, Any] | None:
    """Load only the active account fields needed for password verification."""
    return users_collection.find_one(
        {"_id": user_id, "is_active": True},
        {"password": 1},
    )


def update_user(user_id: Any, first_name: str, last_name: str, updated_at: datetime) -> bool:
    result = users_collection.update_one(
        {"_id": user_id, "is_active": True},
        {"$set": {"first_name": first_name, "last_name": last_name, "updated_at": updated_at}},
    )
    return result.matched_count == 1


def update_email(user_id: Any, email: str, updated_at: datetime) -> bool:
    result = users_collection.update_one(
        {"_id": user_id, "is_active": True},
        {"$set": {"email": email, "updated_at": updated_at}},
    )
    return result.matched_count == 1


def update_password(user_id: Any, hashed_password: str, updated_at: datetime) -> bool:
    result = users_collection.update_one(
        {"_id": user_id, "is_active": True},
        {"$set": {"password": hashed_password, "updated_at": updated_at}},
    )
    return result.matched_count == 1


password_resets_collection: Collection[dict[str, Any]] = db["password_resets"]


def create_reset_index() -> None:
    password_resets_collection.create_index("token_hash", unique=True, name="resets_token_unique")


def insert_password_reset(document: dict[str, Any]) -> None:
    password_resets_collection.insert_one(document)


def invalidate_password_resets(user_id: Any) -> None:
    password_resets_collection.update_many({"user_id": user_id, "used": False}, {"$set": {"used": True}})


def find_password_reset(token_hash: str) -> dict[str, Any] | None:
    return password_resets_collection.find_one(
        {"token_hash": token_hash, "used": False, "expires_at": {"$gt": datetime.now(timezone.utc)}},
    )


def consume_password_reset_token(token_hash: str) -> dict[str, Any] | None:
    """Marque le jeton utilisé de manière atomique et renvoie son document."""
    return password_resets_collection.find_one_and_update(
        {"token_hash": token_hash, "used": False, "expires_at": {"$gt": datetime.now(timezone.utc)}},
        {"$set": {"used": True}},
    )


def create_email_index() -> None:
    users_collection.create_index("email", unique=True, name="users_email_unique")


def list_users_for_admin(search: str = "", role: str = "", active: str = "") -> list[dict[str, Any]]:
    import re as _re

    query: dict[str, Any] = {}
    if search:
        escaped = _re.escape(search)
        query["$or"] = [
            {"email": {"$regex": escaped, "$options": "i"}},
            {"first_name": {"$regex": escaped, "$options": "i"}},
            {"last_name": {"$regex": escaped, "$options": "i"}},
        ]
    if role in {"student", "instructor", "mentor", "admin"}:
        query["role"] = role
    if active in {"yes", "no"}:
        query["is_active"] = active == "yes"
    return list(
        users_collection.find(
            query,
            {"password": 0},
        ).sort("created_at", -1)
    )


def set_user_role(user_id: Any, role: str, updated_at: datetime) -> bool:
    result = users_collection.update_one({"_id": user_id}, {"$set": {"role": role, "updated_at": updated_at}})
    return result.matched_count == 1


def set_user_active(user_id: Any, is_active: bool, updated_at: datetime) -> bool:
    result = users_collection.update_one({"_id": user_id}, {"$set": {"is_active": is_active, "updated_at": updated_at}})
    return result.matched_count == 1
