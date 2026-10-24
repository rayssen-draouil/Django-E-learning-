from __future__ import annotations

from typing import Any

from bson import ObjectId

from apps.mentors.notifications import get_unread_count
from apps.users.services import get_active_user


def auth_user_context(request: Any) -> dict[str, Any]:
    user_id = request.session.get("user_id")
    if not user_id:
        return {
            "current_user": None,
            "user_role": None,
            "unread_notifications_count": 0,
            "is_student": False,
            "is_mentor": False,
            "is_instructor": False,
            "is_admin": False,
        }

    user = getattr(request, "mongo_user", None)
    if not user:
        try:
            user = get_active_user(user_id)
            request.mongo_user = user
        except Exception:
            user = None

    if not user:
        return {
            "current_user": None,
            "user_role": None,
            "unread_notifications_count": 0,
            "is_student": False,
            "is_mentor": False,
            "is_instructor": False,
            "is_admin": False,
        }

    role = user.get("role", "student")
    uid = user.get("_id")
    unread_count = get_unread_count(uid) if uid else 0

    return {
        "current_user": user,
        "user_role": role,
        "unread_notifications_count": unread_count,
        "is_student": role == "student",
        "is_mentor": role == "mentor",
        "is_instructor": role == "instructor",
        "is_admin": role == "admin",
    }
