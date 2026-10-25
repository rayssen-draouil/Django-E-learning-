from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.errors import PyMongoError

from . import repositories


class QuizNotFoundError(Exception):
    pass


class QuizStorageError(Exception):
    pass


class QuizPermissionError(Exception):
    pass


class QuizSubmissionError(Exception):
    pass


def parse_object_id(value: Any) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise QuizNotFoundError
    return ObjectId(value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _can_manage(quiz: dict[str, Any], user: dict[str, Any]) -> bool:
    return user.get("role") == "admin" or quiz.get("created_by") == user.get("_id")


def list_author_quizzes(user: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return repositories.list_quizzes(created_by=None if user.get("role") == "admin" else user["_id"])
    except PyMongoError as error:
        raise QuizStorageError from error


def list_published_quizzes() -> list[dict[str, Any]]:
    try:
        return repositories.list_quizzes(published_only=True)
    except PyMongoError as error:
        raise QuizStorageError from error


def get_published_quiz(quiz_id: Any) -> dict[str, Any]:
    object_id = parse_object_id(quiz_id)
    try:
        quiz = repositories.get_quiz(object_id)
    except PyMongoError as error:
        raise QuizStorageError from error
    if not quiz or quiz.get("status") != "published":
        raise QuizNotFoundError
    return quiz


def submit_quiz(quiz_id: Any, *, user: dict[str, Any], answers: dict[ObjectId, list[str]]) -> ObjectId:
    quiz = get_published_quiz(quiz_id)
    stored_answers = []
    score = 0.0
    maximum = 0.0
    for question in quiz.get("questions", []):
        question_id = question["_id"]
        selected = {parse_object_id(value) for value in answers.get(question_id, [])}
        valid_options = {option["_id"] for option in question.get("options", [])}
        if not selected.issubset(valid_options):
            raise QuizSubmissionError("A submitted answer does not belong to this question.")
        correct = set(question.get("correct_option_ids", []))
        is_correct = selected == correct
        points = float(question.get("points", 1))
        maximum += points
        if is_correct:
            score += points
        stored_answers.append({
            "question_id": question_id,
            "prompt": question["prompt"],
            "selected_option_ids": sorted(selected),
            "correct_option_ids": sorted(correct),
            "options": [{"_id": option["_id"], "text": option["text"]}
                        for option in question.get("options", [])],
            "is_correct": is_correct,
            "points_awarded": points if is_correct else 0,
            "points_possible": points,
        })
    percentage = round(score / maximum * 100) if maximum else 0
    passing_score = quiz.get("passing_score")
    passed = percentage >= (passing_score if passing_score is not None else 50)
    attempt = {
        "_id": ObjectId(),
        "quiz_id": quiz["_id"],
        "quiz_title": quiz["title"],
        "user_id": user["_id"],
        "answers": stored_answers,
        "score": score,
        "max_score": maximum,
        "score_percentage": percentage,
        "passing_score": passing_score,
        "passed": passed,
        "submitted_at": _now(),
    }
    try:
        return repositories.insert_attempt(attempt)
    except PyMongoError as error:
        raise QuizStorageError from error


def get_student_attempt(attempt_id: Any, user: dict[str, Any]) -> dict[str, Any]:
    object_id = parse_object_id(attempt_id)
    try:
        attempt = repositories.get_student_attempt(object_id, user["_id"])
    except PyMongoError as error:
        raise QuizStorageError from error
    if not attempt:
        raise QuizNotFoundError
    return attempt


def get_student_quiz_stats(user: dict[str, Any]) -> dict[str, Any]:
    try:
        attempts = repositories.list_student_attempts(user["_id"])
    except PyMongoError as error:
        raise QuizStorageError from error
    scores = [item.get("score_percentage", 0) for item in attempts]
    question_stats: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        for answer in attempt.get("answers", []):
            key = str(answer["question_id"])
            stat = question_stats.setdefault(key, {"prompt": answer["prompt"], "answered": 0, "correct": 0})
            stat["answered"] += 1
            stat["correct"] += int(answer.get("is_correct", False))
    for stat in question_stats.values():
        stat["accuracy"] = round(stat["correct"] / stat["answered"] * 100) if stat["answered"] else 0
    return {
        "attempts": attempts,
        "attempt_count": len(attempts),
        "quiz_count": len({item["quiz_id"] for item in attempts}),
        "average_score": round(sum(scores) / len(scores)) if scores else 0,
        "best_score": max(scores, default=0),
        "pass_rate": round(sum(bool(item.get("passed")) for item in attempts) / len(attempts) * 100) if attempts else 0,
        "question_stats": sorted(question_stats.values(), key=lambda item: item["prompt"].lower()),
    }


def get_managed_quiz(quiz_id: Any, user: dict[str, Any]) -> dict[str, Any]:
    object_id = parse_object_id(quiz_id)
    try:
        quiz = repositories.get_quiz(object_id)
    except PyMongoError as error:
        raise QuizStorageError from error
    if not quiz:
        raise QuizNotFoundError
    if not _can_manage(quiz, user):
        raise QuizPermissionError
    return quiz


def create_quiz(*, data: dict[str, Any], user: dict[str, Any]) -> ObjectId:
    now = _now()
    document = {
        "_id": ObjectId(),
        "title": data["title"],
        "description": data["description"],
        "created_by": user["_id"],
        "status": data["status"],
        "duration_seconds": data["duration_seconds"],
        "passing_score": data["passing_score"],
        "questions": data["questions"],
        "created_at": now,
        "updated_at": now,
    }
    try:
        return repositories.insert_quiz(document)
    except PyMongoError as error:
        raise QuizStorageError from error


def update_managed_quiz(quiz_id: Any, *, data: dict[str, Any], user: dict[str, Any]) -> None:
    quiz = get_managed_quiz(quiz_id, user)
    fields = {key: data[key] for key in (
        "title", "description", "status", "duration_seconds", "passing_score", "questions"
    )}
    fields["updated_at"] = _now()
    try:
        if not repositories.update_quiz(quiz["_id"], fields):
            raise QuizNotFoundError
    except PyMongoError as error:
        raise QuizStorageError from error


def publish_managed_quiz(quiz_id: Any, *, user: dict[str, Any]) -> None:
    quiz = get_managed_quiz(quiz_id, user)
    if not quiz.get("questions"):
        raise QuizSubmissionError("Add at least one question before publishing the quiz.")
    try:
        if not repositories.update_quiz(quiz["_id"], {"status": "published", "updated_at": _now()}):
            raise QuizNotFoundError
    except PyMongoError as error:
        raise QuizStorageError from error


def delete_managed_quiz(quiz_id: Any, *, user: dict[str, Any]) -> None:
    quiz = get_managed_quiz(quiz_id, user)
    try:
        if not repositories.delete_quiz(quiz["_id"]):
            raise QuizNotFoundError
    except PyMongoError as error:
        raise QuizStorageError from error
