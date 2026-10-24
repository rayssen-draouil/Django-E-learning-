from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from typing import Any

from bson import ObjectId
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError

from apps.users import repositories

USERS_COLLECTION: Collection[dict[str, Any]] = repositories.users_collection
ALLOWED_ROLES = {"student", "instructor", "mentor", "admin"}


class UserServiceError(Exception):
    """Base error for expected user-service failures."""


class DuplicateEmailError(UserServiceError):
    """Raised when an email is already registered."""


class UserNotFoundError(UserServiceError):
    """Raised when a user does not exist or is inactive."""


class UserStorageError(UserServiceError):
    """Raised when MongoDB cannot complete an operation."""


class PasswordChangeError(UserServiceError):
    """Raised when the current password is not confirmed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_users_indexes(collection: Collection[dict[str, Any]] = USERS_COLLECTION) -> None:
    try:
        repositories.create_email_index() if collection is USERS_COLLECTION else collection.create_index(
            "email", unique=True, name="users_email_unique"
        )
        if collection is USERS_COLLECTION:
            repositories.create_reset_index()
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
        if collection is USERS_COLLECTION:
            repositories.insert_user(document)
        else:
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
        if collection is USERS_COLLECTION:
            repositories.insert_user(document)
        else:
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
        document = repositories.find_active_user(email.strip().lower()) if collection is USERS_COLLECTION else collection.find_one(
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
        document = repositories.find_user(object_id) if collection is USERS_COLLECTION else collection.find_one(
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


PASSWORD_RESET_TTL = timedelta(minutes=30)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_password_reset(
    email: str,
    collection: Collection[dict[str, Any]] = USERS_COLLECTION,
) -> str | None:
    """Crée un jeton de réinitialisation à usage unique si le compte existe."""
    normalized_email = email.strip().lower()
    try:
        document = repositories.find_active_user(normalized_email) if collection is USERS_COLLECTION else collection.find_one(
            {"email": normalized_email, "is_active": True}, {"_id": 1}
        )
    except PyMongoError as error:
        raise UserStorageError("Le service utilisateurs est indisponible.") from error
    if not document:
        return None
    token = secrets.token_urlsafe(32)
    created_at = _now()
    reset_document = {
        "_id": ObjectId(),
        "user_id": document["_id"],
        "token_hash": _token_hash(token),
        "created_at": created_at,
        "expires_at": created_at + PASSWORD_RESET_TTL,
        "used": False,
    }
    try:
        repositories.invalidate_password_resets(document["_id"])
        repositories.insert_password_reset(reset_document)
    except PyMongoError as error:
        raise UserStorageError("Impossible de préparer la réinitialisation.") from error
    return token


def is_password_reset_valid(token: str) -> bool:
    try:
        return repositories.find_password_reset(_token_hash(token)) is not None
    except PyMongoError as error:
        raise UserStorageError("Le service utilisateurs est indisponible.") from error


def consume_password_reset(
    token: str,
    password: str,
    collection: Collection[dict[str, Any]] = USERS_COLLECTION,
) -> bool:
    """Valide le jeton, change le mot de passe et rend le jeton inutilisable."""
    token_hash = _token_hash(token)
    try:
        consumed = repositories.consume_password_reset_token(token_hash)
        if consumed is None:
            return False
        if collection is USERS_COLLECTION:
            repositories.update_password(consumed["user_id"], make_password(password), _now())
        else:
            collection.update_one(
                {"_id": consumed["user_id"], "is_active": True},
                {"$set": {"password": make_password(password), "updated_at": _now()}},
            )
    except PyMongoError as error:
        raise UserStorageError("Impossible de changer le mot de passe.") from error
    return True


ADMIN_ROLE_LABELS = {"admin": "Administrateur", "instructor": "Instructeur", "student": "Étudiant"}


class AdminActionError(UserServiceError):
    """Raised when an administrator tries to break the account rules."""


def admin_list_users(*, search: str = "", role: str = "", active: str = "") -> list[dict[str, Any]]:
    """Liste de gestion (sans mot de passe), la plus récente d'abord."""
    try:
        return repositories.list_users_for_admin(search=search, role=role, active=active)
    except PyMongoError as error:
        raise UserStorageError("Service utilisateurs indisponible.") from error


def admin_change_role(user_id: str, role: str, acting_user: dict[str, Any]) -> None:
    if role not in ALLOWED_ROLES:
        raise AdminActionError("Rôle inconnu.")
    if str(acting_user["_id"]) == str(user_id) and role != "admin":
        raise AdminActionError("Vous ne pouvez pas retirer votre propre rôle administrateur.")
    try:
        matched = repositories.set_user_role(ObjectId(user_id), role, _now())
    except (TypeError, ValueError):
        raise UserNotFoundError from None
    except PyMongoError as error:
        raise UserStorageError("Changement de rôle impossible pour le moment.") from error
    if not matched:
        raise UserNotFoundError


def admin_set_active(user_id: str, is_active: bool, acting_user: dict[str, Any]) -> None:
    if str(acting_user["_id"]) == str(user_id) and not is_active:
        raise AdminActionError("Vous ne pouvez pas désactiver votre propre compte.")
    try:
        matched = repositories.set_user_active(ObjectId(user_id), is_active, _now())
    except (TypeError, ValueError):
        raise UserNotFoundError from None
    except PyMongoError as error:
        raise UserStorageError("Changement de statut impossible pour le moment.") from error
    if not matched:
        raise UserNotFoundError


def admin_create_account(
    *,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
    role: str = "instructor",
) -> dict[str, Any]:
    """Création de compte depuis la console admin (rôle maîtrisé, e-mail unique)."""
    if role not in ALLOWED_ROLES:
        raise AdminActionError("Rôle inconnu.")
    if len(password) < 8:
        raise AdminActionError("Le mot de passe temporaire doit faire au moins 8 caractères.")
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
        repositories.insert_user(document)
    except DuplicateKeyError as error:
        raise DuplicateEmailError from error
    except PyMongoError as error:
        raise UserStorageError("Impossible d'enregistrer le compte.") from error
    return _public_user(document)


def update_user_profile(
    user_id: str,
    *,
    first_name: str,
    last_name: str,
    collection: Collection[dict[str, Any]] = USERS_COLLECTION,
) -> dict[str, Any]:
    try:
        if collection is USERS_COLLECTION:
            matched = repositories.update_user(
                ObjectId(user_id), first_name.strip(), last_name.strip(), _now()
            )
        else:
            result = collection.update_one(
            {"_id": ObjectId(user_id), "is_active": True},
            {"$set": {
                "first_name": first_name.strip(),
                "last_name": last_name.strip(),
                "updated_at": _now(),
            }},
            )
            matched = result.matched_count == 1
        if not matched:
            raise UserNotFoundError
        document = repositories.find_user(ObjectId(user_id)) if collection is USERS_COLLECTION else collection.find_one({"_id": ObjectId(user_id)}, {"password": 0})
    except (TypeError, ValueError):
        raise UserNotFoundError from None
    except PyMongoError as error:
        raise UserStorageError("Impossible de modifier le profil.") from error
    if not document:
        raise UserNotFoundError
    return _public_user(document)


def change_email(
    user_id: str,
    *,
    current_password: str,
    new_email: str,
    collection: Collection[dict[str, Any]] = USERS_COLLECTION,
) -> dict[str, Any]:
    """Change the login email after confirming the account password."""
    normalized_email = new_email.strip().lower()
    try:
        document = repositories.find_user_for_password_check(ObjectId(user_id)) if collection is USERS_COLLECTION else collection.find_one(
            {"_id": ObjectId(user_id), "is_active": True}
        )
    except (TypeError, ValueError):
        raise UserNotFoundError from None
    except PyMongoError as error:
        raise UserStorageError("Impossible de modifier l'adresse e-mail.") from error
    if not document:
        raise UserNotFoundError
    if not check_password(current_password, document.get("password", "")):
        raise PasswordChangeError("Le mot de passe actuel est incorrect.")

    try:
        if collection is USERS_COLLECTION:
            matched = repositories.update_email(ObjectId(user_id), normalized_email, _now())
            updated = repositories.find_user(ObjectId(user_id)) if matched else None
        else:
            result = collection.update_one(
                {"_id": ObjectId(user_id), "is_active": True},
                {"$set": {"email": normalized_email, "updated_at": _now()}},
            )
            matched = result.matched_count == 1
            updated = collection.find_one({"_id": ObjectId(user_id), "is_active": True}) if matched else None
    except DuplicateKeyError as error:
        raise DuplicateEmailError from error
    except (TypeError, ValueError):
        raise UserNotFoundError from None
    except PyMongoError as error:
        raise UserStorageError("Impossible de modifier l'adresse e-mail.") from error
    if not matched or not updated:
        raise UserNotFoundError
    return _public_user(updated)


def change_password(
    user_id: str,
    *,
    current_password: str,
    new_password: str,
    collection: Collection[dict[str, Any]] = USERS_COLLECTION,
) -> None:
    """Change le mot de passe d'un compte : l'actuel doit être confirmé."""
    try:
        document = repositories.find_user_for_password_check(ObjectId(user_id)) if collection is USERS_COLLECTION else collection.find_one(
            {"_id": ObjectId(user_id)}
        )
    except (TypeError, ValueError):
        raise UserNotFoundError from None
    except PyMongoError as error:
        raise UserStorageError("Impossible de modifier le mot de passe.") from error
    if not document:
        raise UserNotFoundError
    if not check_password(current_password, document.get("password", "")):
        raise PasswordChangeError("Le mot de passe actuel est incorrect.")
    try:
        if collection is USERS_COLLECTION:
            matched = repositories.update_password(
                ObjectId(user_id), make_password(new_password), _now()
            )
        else:
            matched = collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {"password": make_password(new_password), "updated_at": _now()}},
            ).matched_count == 1
        if not matched:
            raise UserNotFoundError
    except (TypeError, ValueError):
        raise UserNotFoundError from None
    except PyMongoError as error:
        raise UserStorageError("Impossible de modifier le mot de passe.") from error
    # On coupe d'éventuelles demandes de réinitialisation en attente.
    try:
        repositories.invalidate_password_resets(ObjectId(user_id))
    except PyMongoError as error:
        raise UserStorageError("Impossible de couvrir les demandes de reset.") from error
