from __future__ import annotations

from datetime import datetime, timezone
import unicodedata
from typing import Any

from bson import ObjectId
from pymongo.errors import PyMongoError

from apps.courses.catalog import COURSES_BY_ID
from apps.users import repositories as user_repositories
from . import repositories
from .options import (
    LEVEL_CHOICES,
    MANAGEMENT_SORT_CHOICES,
    PUBLIC_SORT_CHOICES,
    STATUS_FILTER_CHOICES,
    THEME_CHOICES,
)


class LearningPathNotFoundError(Exception):
    pass


class LearningPathStorageError(Exception):
    pass


class LearningPathPermissionError(Exception):
    pass


class LearningPathEnrollmentRequiredError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_path_id(value: Any) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise LearningPathNotFoundError
    return ObjectId(value)


def _can_manage(learning_path: dict[str, Any], user: dict[str, Any]) -> bool:
    return user.get("role") == "admin" or learning_path.get("created_by") == user.get("_id")


def _with_courses(learning_path: dict[str, Any]) -> dict[str, Any]:
    result = dict(learning_path)
    result["id"] = str(result["_id"])
    result.setdefault("theme", "other")
    result.setdefault("level", "beginner")
    theme_labels = dict(THEME_CHOICES)
    level_labels = dict(LEVEL_CHOICES)
    result["theme_label"] = theme_labels.get(result["theme"], theme_labels["other"])
    result["level_label"] = level_labels.get(result["level"], level_labels["beginner"])
    result["courses"] = [
        COURSES_BY_ID[course_id]
        for course_id in result.get("course_ids", [])
        if course_id in COURSES_BY_ID
    ]
    return result


def _normalize_search(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _datetime_sort_key(path: dict[str, Any], field: str) -> float:
    value = path.get(field) or path.get("updated_at" if field == "created_at" else "created_at")
    if not isinstance(value, datetime):
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _sort_learning_paths(paths: list[dict[str, Any]], sort: str, choices: tuple) -> list[dict[str, Any]]:
    valid_sorts = {value for value, _label in choices}
    sort = sort if sort in valid_sorts else "recent"
    if sort == "title_asc":
        return sorted(paths, key=lambda path: _normalize_search(path.get("title", "")))
    if sort == "title_desc":
        return sorted(paths, key=lambda path: _normalize_search(path.get("title", "")), reverse=True)
    if sort == "oldest":
        return sorted(paths, key=lambda path: _datetime_sort_key(path, "created_at"))
    return sorted(
        paths,
        key=lambda path: _datetime_sort_key(path, "updated_at"),
        reverse=True,
    )


def _matches_search(path: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    searchable = " ".join([
        path.get("title", ""),
        path.get("description", ""),
        path.get("theme_label", ""),
        path.get("level_label", ""),
        *(
            " ".join((course.get("title", ""), course.get("description", "")))
            for course in path.get("courses", [])
        ),
    ])
    normalized_searchable = _normalize_search(searchable)
    return all(term in normalized_searchable for term in query.split())


def _progress(learning_path: dict[str, Any], enrollment: dict[str, Any]) -> dict[str, Any]:
    course_ids = learning_path.get("course_ids", [])
    completed_ids = set(enrollment.get("completed_course_ids", []))
    completed = [course_id for course_id in course_ids if course_id in completed_ids]
    total = len(course_ids)
    return {
        "completed_course_ids": completed,
        "completed_count": len(completed),
        "total_courses": total,
        "progress_percentage": round(len(completed) / total * 100) if total else 0,
        "is_complete": bool(total) and len(completed) == total,
    }


def _with_progress(learning_path: dict[str, Any], enrollment: dict[str, Any]) -> dict[str, Any]:
    result = _with_courses(learning_path)
    result.update(_progress(learning_path, enrollment))
    result["enrolled_at"] = enrollment["enrolled_at"]
    return result


def list_published_learning_paths(
    *,
    search: str = "",
    theme: str = "",
    level: str = "",
    sort: str = "recent",
) -> list[dict[str, Any]]:
    try:
        paths = repositories.list_learning_paths(published_only=True)
    except PyMongoError as error:
        raise LearningPathStorageError from error
    results = [_with_courses(path) for path in paths]
    valid_themes = {value for value, _label in THEME_CHOICES}
    valid_levels = {value for value, _label in LEVEL_CHOICES}
    normalized_query = _normalize_search(search.strip())
    results = [
        path for path in results
        if (not normalized_query or _matches_search(path, normalized_query))
        and (not theme or theme not in valid_themes or path["theme"] == theme)
        and (not level or level not in valid_levels or path["level"] == level)
    ]
    return _sort_learning_paths(results, sort, PUBLIC_SORT_CHOICES)


def list_managed_learning_paths(
    user: dict[str, Any],
    *,
    search: str = "",
    status: str = "",
    sort: str = "recent",
) -> list[dict[str, Any]]:
    try:
        documents = repositories.list_learning_paths(
            created_by=None if user.get("role") == "admin" else user["_id"]
        )
        counts = repositories.count_enrollments([path["_id"] for path in documents])
    except PyMongoError as error:
        raise LearningPathStorageError from error
    paths = [_with_courses(path) for path in documents]
    for path, document in zip(paths, documents):
        path["enrollment_count"] = counts.get(document["_id"], 0)
    valid_statuses = {value for value, _label in STATUS_FILTER_CHOICES if value}
    normalized_query = _normalize_search(search.strip())
    results = [
        path for path in paths
        if (not normalized_query or _matches_search(path, normalized_query))
        and (not status or status not in valid_statuses or path.get("status") == status)
    ]
    return _sort_learning_paths(results, sort, MANAGEMENT_SORT_CHOICES)


def get_learning_path_statistics(user: dict[str, Any]) -> dict[str, Any]:
    try:
        documents = repositories.list_learning_paths(
            created_by=None if user.get("role") == "admin" else user["_id"]
        )
        path_ids = [path["_id"] for path in documents]
        enrollments = repositories.list_enrollments_for_paths(path_ids)
    except PyMongoError as error:
        raise LearningPathStorageError from error

    paths_by_id = {path["_id"]: path for path in documents}
    enrollment_counts: dict[ObjectId, int] = {}
    completed_enrollments = 0
    progress_total = 0
    for enrollment in enrollments:
        path = paths_by_id.get(enrollment.get("learning_path_id"))
        if path is None:
            continue
        progress = _progress(path, enrollment)
        path_id = path["_id"]
        enrollment_counts[path_id] = enrollment_counts.get(path_id, 0) + 1
        completed_enrollments += int(progress["is_complete"])
        progress_total += progress["progress_percentage"]

    total_enrollments = sum(enrollment_counts.values())
    popular_paths = [
        {
            **_with_courses(path),
            "enrollment_count": enrollment_counts[path["_id"]],
        }
        for path in documents
        if enrollment_counts.get(path["_id"], 0)
    ]
    popular_paths.sort(
        key=lambda path: (
            -path["enrollment_count"],
            _normalize_search(path["title"]),
        )
    )
    most_enrollments = popular_paths[0]["enrollment_count"] if popular_paths else 0
    for path in popular_paths:
        path["enrollment_percentage"] = round(
            path["enrollment_count"] / most_enrollments * 100
        ) if most_enrollments else 0
    return {
        "total_paths": len(documents),
        "published_paths": sum(path.get("status") == "published" for path in documents),
        "draft_paths": sum(path.get("status") == "draft" for path in documents),
        "total_enrollments": total_enrollments,
        "completed_enrollments": completed_enrollments,
        "completion_rate": round(
            completed_enrollments / total_enrollments * 100
        ) if total_enrollments else 0,
        "average_progress": round(
            progress_total / total_enrollments
        ) if total_enrollments else 0,
        "popular_paths": popular_paths[:5],
    }


def get_published_learning_path(path_id: Any) -> dict[str, Any]:
    object_id = _parse_path_id(path_id)
    try:
        learning_path = repositories.get_learning_path(object_id)
    except PyMongoError as error:
        raise LearningPathStorageError from error
    if not learning_path or learning_path.get("status") != "published":
        raise LearningPathNotFoundError
    return _with_courses(learning_path)


def enroll_in_learning_path(path_id: Any, user: dict[str, Any]) -> bool:
    learning_path = get_published_learning_path(path_id)
    try:
        return repositories.enroll_student(
            ObjectId(learning_path["id"]),
            user["_id"],
            _now(),
        )
    except PyMongoError as error:
        raise LearningPathStorageError from error


def get_student_learning_paths(user: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        enrollments = repositories.list_student_enrollments(user["_id"])
        results = []
        for enrollment in enrollments:
            learning_path = repositories.get_learning_path(enrollment["learning_path_id"])
            if learning_path and learning_path.get("status") == "published":
                results.append(_with_progress(learning_path, enrollment))
    except PyMongoError as error:
        raise LearningPathStorageError from error
    return results


def get_student_learning_path(path_id: Any, user: dict[str, Any]) -> dict[str, Any]:
    learning_path = get_published_learning_path(path_id)
    try:
        enrollment = repositories.get_enrollment(
            ObjectId(learning_path["id"]),
            user["_id"],
        )
    except PyMongoError as error:
        raise LearningPathStorageError from error
    if not enrollment:
        raise LearningPathEnrollmentRequiredError
    result = _with_progress(learning_path, enrollment)
    result["courses"] = [
        {
            **course,
            "is_completed": course["id"] in result["completed_course_ids"],
        }
        for course in result["courses"]
    ]
    return result


def set_student_course_completion(
    path_id: Any,
    course_id: Any,
    *,
    user: dict[str, Any],
    completed: bool,
) -> None:
    learning_path = get_published_learning_path(path_id)
    if not isinstance(course_id, str) or course_id not in learning_path.get("course_ids", []):
        raise LearningPathNotFoundError
    path_object_id = ObjectId(learning_path["id"])
    try:
        if not repositories.get_enrollment(path_object_id, user["_id"]):
            raise LearningPathEnrollmentRequiredError
        if not repositories.set_course_completion(
            path_object_id,
            user["_id"],
            course_id,
            completed,
            _now(),
        ):
            raise LearningPathEnrollmentRequiredError
    except PyMongoError as error:
        raise LearningPathStorageError from error


def list_learning_path_learners(path_id: Any, user: dict[str, Any]) -> dict[str, Any]:
    learning_path = get_managed_learning_path(path_id, user)
    object_id = ObjectId(learning_path["id"])
    try:
        enrollments = repositories.list_path_enrollments(object_id)
        learners = user_repositories.find_active_users([
            enrollment["user_id"] for enrollment in enrollments
        ])
    except PyMongoError as error:
        raise LearningPathStorageError from error
    users_by_id = {learner["_id"]: learner for learner in learners}
    learner_progress = []
    for enrollment in enrollments:
        learner = users_by_id.get(enrollment["user_id"])
        progress = _progress(learning_path, enrollment)
        learner_progress.append({
            "name": (
                f'{learner.get("first_name", "")} {learner.get("last_name", "")}'.strip()
                if learner else "Compte apprenant indisponible"
            ),
            **progress,
            "enrolled_at": enrollment.get("enrolled_at"),
        })
    return {
        "learning_path": learning_path,
        "learners": learner_progress,
    }


def get_managed_learning_path(path_id: Any, user: dict[str, Any]) -> dict[str, Any]:
    object_id = _parse_path_id(path_id)
    try:
        learning_path = repositories.get_learning_path(object_id)
    except PyMongoError as error:
        raise LearningPathStorageError from error
    if not learning_path:
        raise LearningPathNotFoundError
    if not _can_manage(learning_path, user):
        raise LearningPathPermissionError
    return _with_courses(learning_path)


def create_learning_path(*, data: dict[str, Any], user: dict[str, Any]) -> ObjectId:
    timestamp = _now()
    document = {
        **data,
        "created_by": user["_id"],
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    try:
        return repositories.insert_learning_path(document)
    except PyMongoError as error:
        raise LearningPathStorageError from error


def update_learning_path(
    path_id: Any, *, data: dict[str, Any], user: dict[str, Any]
) -> None:
    learning_path = get_managed_learning_path(path_id, user)
    try:
        if not repositories.update_learning_path(
            ObjectId(learning_path["id"]),
            {**data, "updated_at": _now()},
        ):
            raise LearningPathNotFoundError
    except PyMongoError as error:
        raise LearningPathStorageError from error


def delete_learning_path(path_id: Any, *, user: dict[str, Any]) -> None:
    learning_path = get_managed_learning_path(path_id, user)
    try:
        if not repositories.delete_learning_path(ObjectId(learning_path["id"])):
            raise LearningPathNotFoundError
    except PyMongoError as error:
        raise LearningPathStorageError from error
