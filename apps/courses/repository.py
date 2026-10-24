from __future__ import annotations

import re
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.collection import Collection

from config.mongodb import db

courses_collection: Collection[dict[str, Any]] = db["courses"]


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
        query["instructor_id"] = instructor_id
    if published_only:
        query["status"] = "published"
    elif status:
        query["status"] = status
    if category:
        query["category"] = category
    if level:
        query["level"] = level
    search = (search or "").strip()
    if search:
        escaped_search = re.escape(search)
        query["$or"] = [
            {"title": {"$regex": escaped_search, "$options": "i"}},
            {"category": {"$regex": escaped_search, "$options": "i"}},
        ]
    return query


def list_courses(
    *,
    instructor_id: ObjectId | None = None,
    status: str | None = None,
    published_only: bool = False,
    search: str = "",
    category: str = "",
    level: str = "",
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
    cursor = courses_collection.find(query).sort([("updated_at", DESCENDING), ("_id", DESCENDING)])
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
    return courses_collection.find_one({"slug": slug})


def insert_course(document: dict[str, Any]) -> ObjectId:
    return courses_collection.insert_one(document).inserted_id


def update_course(course_id: ObjectId, fields: dict[str, Any]) -> bool:
    result = courses_collection.update_one({"_id": course_id}, {"$set": fields})
    return result.matched_count == 1


def delete_course(course_id: ObjectId) -> bool:
    return courses_collection.delete_one({"_id": course_id}).deleted_count == 1


def slug_exists(slug: str, *, exclude_id: ObjectId | None = None) -> bool:
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
        [("title", TEXT), ("description", TEXT)],
        name="courses_text",
        default_language="french",
    )
