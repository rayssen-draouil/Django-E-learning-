from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from typing import Any

from bson import ObjectId
from django.utils.text import slugify
from pymongo.errors import DuplicateKeyError, PyMongoError

from . import repository

PAGE_SIZE = 9
MANAGER_ROLES = {"instructor", "admin"}


class CourseNotFoundError(Exception):
    pass


class CourseStorageError(Exception):
    pass


class CoursePermissionError(Exception):
    pass


def parse_object_id(value: Any) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise CourseNotFoundError
    return ObjectId(value)


def can_manage(course: dict[str, Any], user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    return user.get("role") == "admin" or course.get("instructor_id") == user.get("_id")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_manager(user: dict[str, Any] | None) -> None:
    if not user or user.get("role") not in MANAGER_ROLES:
        raise CoursePermissionError


def ensure_unique_slug(base: str, *, exclude_id: ObjectId | None = None) -> str:
    root = slugify(base or "")[:160] or "cours"
    candidate = root
    suffix = 2
    while repository.slug_exists(candidate, exclude_id=exclude_id):
        suffix_label = f"-{suffix}"
        candidate = f"{root[:180 - len(suffix_label)]}{suffix_label}"
        suffix += 1
    return candidate


def list_public_courses(
    *,
    search: str = "",
    category: str = "",
    level: str = "",
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> dict[str, Any]:
    page = max(1, page)
    try:
        total = repository.count_courses(
            published_only=True, search=search, category=category, level=level
        )
        page_count = max(1, ceil(total / page_size))
        page = min(page, page_count)
        courses = repository.list_courses(
            published_only=True,
            search=search,
            category=category,
            level=level,
            skip=(page - 1) * page_size,
            limit=page_size,
        )
    except PyMongoError as error:
        raise CourseStorageError("Les cours sont temporairement indisponibles.") from error
    return {
        "courses": courses,
        "total": total,
        "page": page,
        "page_count": page_count,
        "previous_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < page_count else None,
    }


def list_managed_courses(user: dict[str, Any]) -> list[dict[str, Any]]:
    _require_manager(user)
    instructor_id = None if user.get("role") == "admin" else user.get("_id")
    try:
        return repository.list_courses(instructor_id=instructor_id)
    except PyMongoError as error:
        raise CourseStorageError("Vos cours sont temporairement indisponibles.") from error


def get_course_detail(slug: str, user: dict[str, Any] | None) -> dict[str, Any]:
    if not slug or len(slug) > 200:
        raise CourseNotFoundError
    try:
        course = repository.get_course_by_slug(slug)
    except PyMongoError as error:
        raise CourseStorageError("Ce cours est temporairement indisponible.") from error
    if not course:
        raise CourseNotFoundError
    if course.get("status") != "published" and not can_manage(course, user):
        raise CourseNotFoundError
    return course


def get_managed_course(course_id: Any, user: dict[str, Any]) -> dict[str, Any]:
    _require_manager(user)
    object_id = parse_object_id(course_id)
    try:
        course = repository.get_course(object_id)
    except PyMongoError as error:
        raise CourseStorageError("Ce cours est temporairement indisponible.") from error
    if not course:
        raise CourseNotFoundError
    if not can_manage(course, user):
        raise CoursePermissionError
    return course


def create_course(*, data: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    _require_manager(user)
    now = _now()
    try:
        slug = ensure_unique_slug(data.get("slug") or data["title"])
    except PyMongoError as error:
        raise CourseStorageError("Impossible de préparer l'identifiant du cours.") from error
    instructor_name = " ".join(
        part for part in (user.get("first_name"), user.get("last_name")) if part
    ).strip() or user.get("email") or "Instructeur"
    document = {
        "_id": ObjectId(),
        "title": data["title"],
        "slug": slug,
        "description": data["description"],
        "objectives": data.get("objectives") or [],
        "category": data["category"],
        "level": data["level"],
        "duration_hours": float(data.get("duration_hours") or 0),
        "language": data.get("language") or "fr",
        "price": float(data.get("price") or 0),
        "is_free": bool(data.get("is_free")),
        "thumbnail": data.get("thumbnail") or "",
        "thumbnail_url": data.get("thumbnail_url") or data.get("thumbnail") or "",
        "status": data.get("status", "draft"),
        "instructor_id": user["_id"],
        "instructor_name": instructor_name,
        "created_at": now,
        "updated_at": now,
    }
    try:
        repository.insert_course(document)
    except DuplicateKeyError as error:
        raise CourseStorageError(
            "Un cours utilise déjà cet identifiant d'URL. Modifiez le slug."
        ) from error
    except PyMongoError as error:
        raise CourseStorageError("Impossible d'enregistrer le cours pour le moment.") from error
    return document


def update_managed_course(course_id: Any, *, data: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    course = get_managed_course(course_id, user)
    try:
        if data.get("slug"):
            slug = ensure_unique_slug(data["slug"], exclude_id=course["_id"])
        else:
            slug = course["slug"]
    except PyMongoError as error:
        raise CourseStorageError("Impossible de préparer l'identifiant du cours.") from error
    fields = {
        "title": data["title"],
        "slug": slug,
        "description": data["description"],
        "objectives": data.get("objectives") or [],
        "category": data["category"],
        "level": data["level"],
        "duration_hours": float(data.get("duration_hours") or 0),
        "language": data.get("language") or course.get("language", "fr"),
        "price": float(data.get("price") or 0),
        "is_free": bool(data.get("is_free")),
        "thumbnail": data.get("thumbnail") or "",
        "thumbnail_url": data.get("thumbnail_url") or data.get("thumbnail") or "",
        "status": data.get("status", course.get("status", "draft")),
        "updated_at": _now(),
    }
    try:
        if not repository.update_course(course["_id"], fields):
            raise CourseNotFoundError
    except DuplicateKeyError as error:
        raise CourseStorageError(
            "Un cours utilise déjà cet identifiant d'URL. Modifiez le slug."
        ) from error
    except PyMongoError as error:
        raise CourseStorageError("Impossible d'enregistrer les modifications.") from error
    updated = dict(course)
    updated.update(fields)
    return updated


def toggle_managed_course_status(course_id: Any, *, user: dict[str, Any]) -> dict[str, Any]:
    course = get_managed_course(course_id, user)
    new_status = "draft" if course.get("status") == "published" else "published"
    try:
        if not repository.update_course(
            course["_id"], {"status": new_status, "updated_at": _now()}
        ):
            raise CourseNotFoundError
    except PyMongoError as error:
        raise CourseStorageError("Impossible de modifier la publication du cours.") from error
    updated = dict(course)
    updated["status"] = new_status
    return updated


def delete_managed_course(course_id: Any, *, user: dict[str, Any]) -> None:
    course = get_managed_course(course_id, user)
    try:
        if not repository.delete_course(course["_id"]):
            raise CourseNotFoundError
    except PyMongoError as error:
        raise CourseStorageError("Impossible de supprimer le cours pour le moment.") from error
