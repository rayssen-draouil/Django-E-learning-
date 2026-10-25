from __future__ import annotations

import re
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.collection import Collection

from config.mongodb import db

courses_collection: Collection[dict[str, Any]] = db["courses"]
course_translations_collection: Collection[dict[str, Any]] = db["course_translations"]


def _build_query(
    *,
    instructor_id: ObjectId | None = None,
    status: str | None = None,
    published_only: bool = False,
    search: str = "",
    category: str = "",
    level: str = "",
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if instructor_id is not None:
        if isinstance(instructor_id, ObjectId):
            query["instructor_id"] = instructor_id
        else:
            query["_id"] = None
    if published_only:
        query["status"] = "published"
    elif status:
        if isinstance(status, str) and not status.startswith("$"):
            query["status"] = status
        else:
            query["_id"] = None
    if category:
        if isinstance(category, str) and not category.startswith("$"):
            query["category"] = category
        else:
            query["_id"] = None
    if level:
        if isinstance(level, str) and not level.startswith("$"):
            query["level"] = level
        else:
            query["_id"] = None
    if search:
        if isinstance(search, str):
            cleaned_search = search.strip()
            if cleaned_search.startswith("$"):
                query["_id"] = None
            elif cleaned_search:
                escaped_search = re.escape(cleaned_search)
                query["$or"] = [
                    {"title": {"$regex": escaped_search, "$options": "i"}},
                    {"category": {"$regex": escaped_search, "$options": "i"}},
                    {"description": {"$regex": escaped_search, "$options": "i"}},
                    {"tags": {"$regex": escaped_search, "$options": "i"}},
                ]
        else:
            query["_id"] = None
    return query


def list_courses(
    *,
    instructor_id: ObjectId | None = None,
    status: str | None = None,
    published_only: bool = False,
    search: str = "",
    category: str = "",
    level: str = "",
    sort_by: str = "recent",
    skip: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    query = _build_query(
        instructor_id=instructor_id,
        status=status,
        published_only=published_only,
        search=search,
        category=category,
        level=level,
    )
    sort_fields = {
        "recent": [("updated_at", DESCENDING), ("_id", DESCENDING)],
        "title": [("title", ASCENDING), ("_id", DESCENDING)],
        "price_asc": [("price", ASCENDING), ("_id", DESCENDING)],
        "duration_asc": [("duration_hours", ASCENDING), ("_id", DESCENDING)],
    }.get(sort_by, [("updated_at", DESCENDING), ("_id", DESCENDING)])
    cursor = courses_collection.find(query).sort(sort_fields)
    if skip:
        cursor = cursor.skip(skip)
    if limit is not None:
        cursor = cursor.limit(limit)
    return list(cursor)


def count_courses(
    *,
    instructor_id: ObjectId | None = None,
    status: str | None = None,
    published_only: bool = False,
    search: str = "",
    category: str = "",
    level: str = "",
) -> int:
    return courses_collection.count_documents(_build_query(
        instructor_id=instructor_id,
        status=status,
        published_only=published_only,
        search=search,
        category=category,
        level=level,
    ))


def get_course(course_id: ObjectId) -> dict[str, Any] | None:
    return courses_collection.find_one({"_id": course_id})


def get_course_by_slug(slug: str) -> dict[str, Any] | None:
    if not isinstance(slug, str) or not slug or slug.startswith("$"):
        return None
    return courses_collection.find_one({"slug": slug})


def insert_course(document: dict[str, Any]) -> ObjectId:
    return courses_collection.insert_one(document).inserted_id


def update_course(course_id: ObjectId, fields: dict[str, Any]) -> bool:
    result = courses_collection.update_one({"_id": course_id}, {"$set": fields})
    return result.matched_count == 1


def delete_course(course_id: ObjectId) -> bool:
    return courses_collection.delete_one({"_id": course_id}).deleted_count == 1


def get_translation_cache(query: dict[str, Any]) -> dict[str, Any] | None:
    return course_translations_collection.find_one(query)


def save_translation_cache(query: dict[str, Any], payload: dict[str, Any]) -> None:
    course_translations_collection.update_one(query, {"$set": payload}, upsert=True)


def delete_course_translations(course_id: ObjectId) -> None:
    course_translations_collection.delete_many({"course_id": course_id})


def slug_exists(slug: str, *, exclude_id: ObjectId | None = None) -> bool:
    if not isinstance(slug, str) or not slug or slug.startswith("$"):
        return False
    query: dict[str, Any] = {"slug": slug}
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    return courses_collection.find_one(query, {"_id": 1}) is not None


def create_indexes() -> None:
    courses_collection.create_index(
        [("slug", ASCENDING)], unique=True, name="courses_slug_unique"
    )
    courses_collection.create_index(
        [("instructor_id", ASCENDING), ("updated_at", DESCENDING)],
        name="courses_instructor_updated",
    )
    courses_collection.create_index(
        [("instructor_id", ASCENDING)], name="courses_instructor"
    )
    courses_collection.create_index(
        [("status", ASCENDING), ("updated_at", DESCENDING)],
        name="courses_status_updated",
    )
    courses_collection.create_index([("status", ASCENDING)], name="courses_status")
    courses_collection.create_index([("category", ASCENDING)], name="courses_category")
    courses_collection.create_index(
        [("title", TEXT), ("description", TEXT), ("tags", TEXT)],
        name="courses_text",
        default_language="french",
    )
    course_translations_collection.create_index(
        [("course_id", ASCENDING), ("lang", ASCENDING), ("course_updated_at", ASCENDING)],
        unique=True,
        name="course_translations_unique",
    )
