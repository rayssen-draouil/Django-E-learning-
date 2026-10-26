from __future__ import annotations

import math
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from django.conf import settings
from pymongo.errors import PyMongoError

from config.mongodb import db

from .ai import get_groq_api_key
from .management.commands.ai_report import (
    DEFAULT_EVAL_DELAY,
    EvaluationError,
    EvaluationNotConfiguredError,
    _load_dataset,
    run_evaluation,
)

try:
    from celery import shared_task
except ImportError:
    # Minimal fallback decorator if Celery is somehow not installed
    def shared_task(func: Callable) -> Callable:
        func.delay = lambda *args, **kwargs: _run_local_fallback(func, *args, **kwargs)
        return func

from .ai import (
    AIError,
    generate_course_content,
    generate_objectives,
    improve_description,
    suggest_meta,
)

# The thread fallback is persisted so a status request can be handled by a
# different runserver worker or survive an autoreload.
ai_tasks_collection = db["course_ai_tasks"]
LAST_DISPATCH_MODE = "thread"

EVALUATION_TASK_KIND = "ai_evaluation"
EVALUATION_LOCK_ID = "__ai_evaluation_lock__"
EVALUATION_COOLDOWN_SECONDS = 600
EVALUATION_STATUSES = ("pending", "running", "done", "error")


class AIEvaluationError(Exception):
    """Base class for background evaluation orchestration errors."""

    code = "internal_error"


class AIEvaluationNotConfiguredError(AIEvaluationError):
    code = "missing_key"


class AIEvaluationDatasetError(AIEvaluationError):
    code = "invalid_dataset"


class AIEvaluationDispatchError(AIEvaluationError):
    code = "dispatch_error"


class AIEvaluationAlreadyRunningError(AIEvaluationError):
    code = "already_running"


class AIEvaluationCooldownError(AIEvaluationError):
    code = "cooldown"

    def __init__(self, remaining_seconds: int) -> None:
        self.remaining_seconds = max(1, int(remaining_seconds))
        minutes, seconds = divmod(self.remaining_seconds, 60)
        if minutes:
            wait = f"{minutes} min {seconds:02d} s"
        else:
            wait = f"{seconds} s"
        super().__init__(
            "Une évaluation a été lancée récemment. "
            f"Réessayez dans {wait} (une évaluation toutes les 10 minutes)."
        )


class LocalTaskWrapper:
    """Mock task handle mimicking Celery's AsyncResult ID."""

    def __init__(self, task_id: str):
        self.id = task_id


def _run_local_fallback(func: Callable, *args: Any, **kwargs: Any) -> LocalTaskWrapper:
    """Run a fallback task in a thread and persist its status in MongoDB."""
    task_id = f"local-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    ai_tasks_collection.delete_many({"expires_at": {"$lte": now}})
    ai_tasks_collection.insert_one({
        "_id": task_id,
        "status": "pending",
        "created_at": now,
        "expires_at": now + timedelta(hours=1),
    })

    def worker():
        try:
            result = func(*args, **kwargs)
            ai_tasks_collection.update_one(
                {"_id": task_id},
                {"$set": {
                    "status": "done",
                    "result": result,
                    "finished_at": datetime.now(timezone.utc),
                }},
            )
        except Exception:
            # Task functions convert expected provider errors to an ``ok``
            # result. Keep unexpected failures generic and never expose a traceback.
            try:
                ai_tasks_collection.update_one(
                    {"_id": task_id},
                    {"$set": {
                        "status": "error",
                        "error": "La génération IA a échoué.",
                        "finished_at": datetime.now(timezone.utc),
                    }},
                )
            except PyMongoError:
                pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return LocalTaskWrapper(task_id)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _ensure_evaluation_lock() -> None:
    ai_tasks_collection.update_one(
        {"_id": EVALUATION_LOCK_ID},
        {
            "$setOnInsert": {
                "active_task_id": "",
                "last_started_at": None,
                "created_at": _utc_now(),
            }
        },
        upsert=True,
    )


def _claim_evaluation_slot(task_id: str) -> None:
    """Atomically reserve the single evaluation slot, honouring the cooldown."""
    for _ in range(3):
        _ensure_evaluation_lock()
        lock = ai_tasks_collection.find_one({"_id": EVALUATION_LOCK_ID}) or {}
        active = str(lock.get("active_task_id") or "")
        if active:
            active_task = ai_tasks_collection.find_one(
                {"_id": active, "kind": EVALUATION_TASK_KIND}
            )
            if active_task and active_task.get("status") in {"pending", "running"}:
                raise AIEvaluationAlreadyRunningError(
                    "Une évaluation est déjà en cours. Attendez sa fin avant de la relancer."
                )
            ai_tasks_collection.update_one(
                {"_id": EVALUATION_LOCK_ID, "active_task_id": active},
                {"$set": {"active_task_id": "", "updated_at": _utc_now()}},
            )
            continue

        now = _utc_now()
        last_started = _as_utc(lock.get("last_started_at"))
        if last_started is not None:
            elapsed = (now - last_started).total_seconds()
            if elapsed < EVALUATION_COOLDOWN_SECONDS:
                raise AIEvaluationCooldownError(
                    math.ceil(EVALUATION_COOLDOWN_SECONDS - elapsed)
                )

        cutoff = now - timedelta(seconds=EVALUATION_COOLDOWN_SECONDS)
        claimed = ai_tasks_collection.find_one_and_update(
            {
                "_id": EVALUATION_LOCK_ID,
                "$and": [
                    {
                        "$or": [
                            {"active_task_id": {"$exists": False}},
                            {"active_task_id": None},
                            {"active_task_id": ""},
                        ]
                    },
                    {
                        "$or": [
                            {"last_started_at": {"$exists": False}},
                            {"last_started_at": None},
                            {"last_started_at": {"$lte": cutoff}},
                        ]
                    },
                ],
            },
            {
                "$set": {
                    "active_task_id": task_id,
                    "last_started_at": now,
                    "updated_at": now,
                }
            },
        )
        if claimed is not None:
            return

    raise AIEvaluationAlreadyRunningError(
        "Une évaluation est déjà en cours. Attendez sa fin avant de la relancer."
    )


def _release_evaluation_lock(task_id: str, *, reset_cooldown: bool = False) -> None:
    fields = {"active_task_id": "", "updated_at": _utc_now()}
    if reset_cooldown:
        # A launch that never started must not block retries for 10 minutes.
        fields["last_started_at"] = None
    try:
        ai_tasks_collection.update_one(
            {"_id": EVALUATION_LOCK_ID, "active_task_id": task_id},
            {"$set": fields},
        )
    except PyMongoError:
        pass


def _update_evaluation_task(task_id: str, **fields: Any) -> None:
    fields["updated_at"] = _utc_now()
    try:
        ai_tasks_collection.update_one(
            {"_id": task_id, "kind": EVALUATION_TASK_KIND},
            {"$set": fields},
        )
    except PyMongoError:
        pass


def _fail_evaluation_task(task_id: str, error_code: str, message: str) -> None:
    _update_evaluation_task(
        task_id,
        status="error",
        error_code=error_code,
        error=message,
        finished_at=_utc_now(),
    )


def _execute_ai_evaluation(task_id: str, delay: float) -> dict[str, Any]:
    _update_evaluation_task(
        task_id,
        status="running",
        error_code="",
        started_at=_utc_now(),
    )
    try:
        def progress(done: int, total: int) -> None:
            _update_evaluation_task(
                task_id,
                status="running",
                done=max(0, int(done)),
                total=max(1, int(total)),
            )

        result = run_evaluation(
            progress_callback=progress,
            delay=delay,
            require_answers=True,
        )
    except EvaluationNotConfiguredError as error:
        message = str(error)
        _fail_evaluation_task(task_id, "missing_key", message)
        return {"ok": False, "error": message, "error_code": "missing_key"}
    except EvaluationError as error:
        code = str(getattr(error, "code", "evaluation_error"))
        message = str(error)
        _fail_evaluation_task(task_id, code, message)
        return {"ok": False, "error": message, "error_code": code}
    except Exception:
        message = "L'évaluation a échoué. Le rapport précédent est conservé."
        _fail_evaluation_task(task_id, "internal_error", message)
        return {"ok": False, "error": message, "error_code": "internal_error"}
    else:
        total = int(result.outcome.total)
        _update_evaluation_task(
            task_id,
            status="done",
            done=total,
            total=total,
            error_code="",
            finished_at=_utc_now(),
            result={
                "ok": True,
                "answered": result.outcome.answered,
                "total": total,
            },
        )
        return {
            "ok": True,
            "answered": result.outcome.answered,
            "total": total,
        }
    finally:
        _release_evaluation_lock(task_id)


def _evaluation_thread_worker(task_id: str, delay: float) -> None:
    try:
        _execute_ai_evaluation(task_id, delay)
    except Exception:
        _fail_evaluation_task(
            task_id,
            "internal_error",
            "L'évaluation a échoué. Le rapport précédent est conservé.",
        )
        _release_evaluation_lock(task_id)


def _dispatch_evaluation_task(task_id: str, delay: float) -> None:
    global LAST_DISPATCH_MODE

    if not bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)):
        try:
            run_ai_evaluation_task.apply_async(
                args=(task_id, delay),
                task_id=task_id,
            )
            LAST_DISPATCH_MODE = "celery"
            return
        except Exception:
            pass

    thread = threading.Thread(
        target=_evaluation_thread_worker,
        args=(task_id, delay),
        daemon=True,
    )
    thread.start()
    LAST_DISPATCH_MODE = "thread"


def start_ai_evaluation(delay: float = DEFAULT_EVAL_DELAY) -> str:
    """Start one real evaluation in the background and return its persisted task id."""
    if not get_groq_api_key():
        raise AIEvaluationNotConfiguredError(
            "La clé GROQ_API_KEY n'est pas configurée. L'évaluation réelle ne peut pas "
            "démarrer ; aucun mode simulé n'est utilisé."
        )

    try:
        total = len(_load_dataset())
    except EvaluationError as error:
        raise AIEvaluationDatasetError(
            "Le jeu d'évaluation est illisible. Aucune évaluation n'a été lancée."
        ) from error

    task_id = f"eval-{uuid.uuid4().hex}"
    try:
        _claim_evaluation_slot(task_id)
    except AIEvaluationError:
        raise
    except PyMongoError as error:
        raise AIEvaluationDispatchError(
            "Impossible de démarrer l'évaluation. Réessayez dans quelques instants."
        ) from error

    now = _utc_now()
    try:
        ai_tasks_collection.insert_one({
            "_id": task_id,
            "kind": EVALUATION_TASK_KIND,
            "status": "pending",
            "done": 0,
            "total": total,
            "error_code": "",
            "created_at": now,
            "updated_at": now,
            "expires_at": now + timedelta(hours=24),
        })
    except PyMongoError as error:
        _release_evaluation_lock(task_id, reset_cooldown=True)
        raise AIEvaluationDispatchError(
            "Impossible de démarrer l'évaluation. Réessayez dans quelques instants."
        ) from error

    try:
        _dispatch_evaluation_task(task_id, delay)
    except Exception as error:
        try:
            ai_tasks_collection.delete_one(
                {"_id": task_id, "kind": EVALUATION_TASK_KIND}
            )
        except PyMongoError:
            pass
        _release_evaluation_lock(task_id, reset_cooldown=True)
        raise AIEvaluationDispatchError(
            "Impossible de démarrer l'évaluation. Réessayez dans quelques instants."
        ) from error

    return task_id


def get_ai_evaluation_status(task_id: str) -> dict[str, Any]:
    """Return the persisted evaluation state used by the polling view."""
    try:
        document = ai_tasks_collection.find_one(
            {"_id": task_id, "kind": EVALUATION_TASK_KIND}
        )
    except PyMongoError:
        return {
            "state": "error",
            "done": 0,
            "total": 0,
            "error_code": "storage_error",
        }
    if not document:
        return {
            "state": "error",
            "done": 0,
            "total": 0,
            "error_code": "unknown_task",
        }

    state = document.get("status")
    if state not in EVALUATION_STATUSES:
        state = "error"
    try:
        done = max(0, int(document.get("done") or 0))
    except (TypeError, ValueError):
        done = 0
    try:
        total = max(0, int(document.get("total") or 0))
    except (TypeError, ValueError):
        total = 0
    if state == "done":
        done = total

    error_code = ""
    if state == "error":
        error_code = str(document.get("error_code") or "internal_error")
    return {
        "state": state,
        "done": done,
        "total": total,
        "error_code": error_code,
    }


def get_ai_execution_mode() -> str:
    """Return 'celery' or 'thread' depending on the last successful dispatch path."""
    return LAST_DISPATCH_MODE


def dispatch_task(task_func: Any, *args: Any, **kwargs: Any) -> str:
    """Dispatch via Celery .delay(), falling back to local thread if broker fails."""
    global LAST_DISPATCH_MODE
    try:
        async_res = task_func.delay(*args, **kwargs)
        LAST_DISPATCH_MODE = "celery"
        return str(async_res.id)
    except Exception:
        LAST_DISPATCH_MODE = "thread"
        wrapper = _run_local_fallback(task_func, *args, **kwargs)
        return wrapper.id


def get_task_result(task_id: str) -> dict[str, Any]:
    """Retrieve a persistent thread result or a Celery AsyncResult."""
    try:
        info = ai_tasks_collection.find_one({"_id": task_id})
    except PyMongoError:
        info = None
    if info:
        status = info.get("status")
        if status == "done":
            result = info.get("result") or {}
            if isinstance(result, dict) and result.get("ok") is False:
                return {"status": "error", "error": result.get("error", "Erreur IA.")}
            return {"status": "done", **result} if isinstance(result, dict) else {
                "status": "done", "result": result
            }
        if status == "error":
            return {"status": "error", "error": info.get("error", "Erreur IA.")}
        return {"status": "pending"}

    # 2. Check Celery AsyncResult
    try:
        from celery.result import AsyncResult
        res = AsyncResult(task_id)
        if res.failed():
            return {"status": "error", "error": "La tâche asynchrone a échoué."}
        if res.successful():
            data = res.result or {}
            if isinstance(data, dict):
                if data.get("ok") is False:
                    return {"status": "error", "error": data.get("error", "La génération IA a échoué.")}
                return {"status": "done", **data}
            return {"status": "done", "result": data}
        return {"status": "pending"}
    except Exception:
        return {"status": "error", "error": "Impossible de récupérer le statut de la tâche."}


# -----------------------------------------------------------------------------
# Celery Tasks
# -----------------------------------------------------------------------------
@shared_task
def generate_course_content_task(title: str, level: str) -> dict[str, Any]:
    try:
        content = generate_course_content(title, level)
        return {"ok": True, **content}
    except AIError as error:
        return {"ok": False, "error": str(error)}
    except Exception:
        return {"ok": False, "error": "Erreur inattendue de l'assistant IA."}


@shared_task
def generate_objectives_task(title: str, description: str = "", level: str = "") -> dict[str, Any]:
    try:
        objectives = generate_objectives(title=title, description=description, level=level)
        return {"ok": True, "objectives": objectives}
    except AIError as error:
        return {"ok": False, "error": str(error)}
    except Exception:
        return {"ok": False, "error": "Erreur inattendue lors de la génération des objectifs."}


@shared_task
def improve_description_task(title: str, description: str) -> dict[str, Any]:
    try:
        improved = improve_description(title=title, description=description)
        return {"ok": True, "improved_description": improved}
    except AIError as error:
        return {"ok": False, "error": str(error)}
    except Exception:
        return {"ok": False, "error": "Erreur inattendue lors de l'amélioration de la description."}


@shared_task
def suggest_meta_task(title: str, description: str = "") -> dict[str, Any]:
    try:
        meta = suggest_meta(title=title, description=description)
        return {"ok": True, **meta}
    except AIError as error:
        return {"ok": False, "error": str(error)}
    except Exception:
        return {"ok": False, "error": "Erreur inattendue lors de la suggestion des métadonnées."}


@shared_task
def run_ai_evaluation_task(task_id: str, delay: float = DEFAULT_EVAL_DELAY) -> dict[str, Any]:
    return _execute_ai_evaluation(task_id, delay)
