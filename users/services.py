from __future__ import annotations

from datetime import datetime, timezone
from hmac import compare_digest
from typing import Any

from bson import ObjectId
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError

from config.mongodb import db

USERS_COLLECTION: Collection[dict[str, Any]] = db["users"]
ALLOWED_ROLES = {"student", "instructor", "admin"}


class UserServiceError(Exception):
    """Base error for expected user-service failures."""


class DuplicateEmailError(UserServiceError):
    """Raised when an email is already registered."""


class UserNotFoundError(UserServiceError):
    """Raised when a user does not exist or is inactive."""


class UserStorageError(UserServiceError):
    """Raised when MongoDB cannot complete an operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_users_indexes(collection: Collection[dict[str, Any]] = USERS_COLLECTION) -> None:
    try:
        collection.create_index("email", unique=True, name="users_email_unique")
    except PyMongoError as error:
        raise UserStorageError("Impossible de préparer le stockage des utilisateurs.") from error


def _public_user(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "_id": document["_id"],
        "first_name": document["first_name"],
        "last_name": document["last_name"],
        "email": document["email"],
        "role": document["role"],
        "is_active": document["is_active"],
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
    }


def create_user(
    *,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
    role: str = "student",
    collection: Collection[dict[str, Any]] = USERS_COLLECTION,
) -> dict[str, Any]:
    if role not in ALLOWED_ROLES or role == "admin":
        raise ValueError("Un compte administrateur ne peut pas être créé par l'inscription.")

    normalized_email = email.strip().lower()
    timestamp = _now()
    document = {
        "_id": ObjectId(),
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "email": normalized_email,
        "password": make_password(password),
        "role": role,
        "is_active": True,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    try:
        ensure_users_indexes(collection)
        collection.insert_one(document)
    except DuplicateKeyError as error:
        raise DuplicateEmailError from error
    except PyMongoError as error:
        raise UserStorageError("Impossible d'enregistrer le compte.") from error
    return _public_user(document)


def create_admin_user(
    *,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
    bootstrap_token: str,
    collection: Collection[dict[str, Any]] = USERS_COLLECTION,
) -> dict[str, Any]:
    configured_token = getattr(settings, "USERS_ADMIN_BOOTSTRAP_TOKEN", "")
    if not configured_token or not compare_digest(bootstrap_token, configured_token):
        raise PermissionError("Provisionnement administrateur non autorisé.")

    normalized_email = email.strip().lower()
    timestamp = _now()
    document = {
        "_id": ObjectId(),
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "email": normalized_email,
        "password": make_password(password),
        "role": "admin",
        "is_active": True,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    try:
        ensure_users_indexes(collection)
        collection.insert_one(document)
    except DuplicateKeyError as error:
        raise DuplicateEmailError from error
    except PyMongoError as error:
        raise UserStorageError("Impossible d'enregistrer le compte administrateur.") from error
    return _public_user(document)


def authenticate_user(
    email: str,
    password: str,
    collection: Collection[dict[str, Any]] = USERS_COLLECTION,
) -> dict[str, Any] | None:
    try:
        document = collection.find_one(
            {"email": email.strip().lower(), "is_active": True},
            {"password": 1, "first_name": 1, "last_name": 1, "email": 1,
             "role": 1, "is_active": 1, "created_at": 1, "updated_at": 1},
        )
    except PyMongoError as error:
        raise UserStorageError("Le service utilisateurs est indisponible.") from error
    if not document or not check_password(password, document.get("password", "")):
        return None
    return _public_user(document)


def get_active_user(
    user_id: str,
    collection: Collection[dict[str, Any]] = USERS_COLLECTION,
) -> dict[str, Any]:
    try:
        object_id = ObjectId(user_id)
        document = collection.find_one(
            {"_id": object_id, "is_active": True},
            {"password": 0},
        )
    except (TypeError, ValueError):
        raise UserNotFoundError from None
    except PyMongoError as error:
        raise UserStorageError("Le service utilisateurs est indisponible.") from error
    if not document:
        raise UserNotFoundError
    return _public_user(document)


def update_user_profile(
    user_id: str,
    *,
    first_name: str,
    last_name: str,
    collection: Collection[dict[str, Any]] = USERS_COLLECTION,
) -> dict[str, Any]:
    try:
        result = collection.update_one(
            {"_id": ObjectId(user_id), "is_active": True},
            {"$set": {
                "first_name": first_name.strip(),
                "last_name": last_name.strip(),
                "updated_at": _now(),
            }},
        )
        if result.matched_count != 1:
            raise UserNotFoundError
        document = collection.find_one({"_id": ObjectId(user_id)}, {"password": 0})
    except (TypeError, ValueError):
        raise UserNotFoundError from None
    except PyMongoError as error:
        raise UserStorageError("Impossible de modifier le profil.") from error
    if not document:
        raise UserNotFoundError
    return _public_user(document)
