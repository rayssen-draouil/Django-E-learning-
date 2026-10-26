"""Tests for the admin-triggered AI evaluation run (report page button)."""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse
from pymongo.errors import PyMongoError

from apps.courses import ai, tasks
from apps.courses.management.commands import ai_report
from apps.courses.management.commands.ai_report import (
    EvaluationDatasetError,
    EvaluationError,
    EvaluationNotConfiguredError,
    _atomic_write_text,
    run_evaluation,
)

from .base import MongoTestCase, user_document
from .test_ai_report_ui import report_file, report_payload

CSRF_TOKEN = "a" * 32
NAMESPACES = ("courses", "users")
VALID_META_JSON = json.dumps({
    "category": "programming",
    "level": "beginner",
    "tags": ["python", "tests"],
})


@contextmanager
def groq_env(key: str = "test-key", model: str = "test-model"):
    with patch.dict(os.environ, {"GROQ_API_KEY": key, "GROQ_MODEL": model}):
        yield


@contextmanager
def report_paths(directory: str):
    markdown = Path(directory) / "report.md"
    structured = Path(directory) / "report.json"
    with patch.object(ai_report, "_REPORT_PATH", markdown), patch.object(
        ai_report, "_REPORT_JSON_PATH", structured
    ):
        yield markdown, structured


def make_eval_task(**overrides):
    now = datetime.now(timezone.utc)
    document = {
        "_id": "eval-test",
        "kind": tasks.EVALUATION_TASK_KIND,
        "status": "pending",
        "done": 0,
        "total": 36,
        "error_code": "",
        "created_at": now,
        "updated_at": now,
        "expires_at": now + timedelta(hours=24),
    }
    document.update(overrides)
    return document


def make_lock(**overrides):
    now = datetime.now(timezone.utc)
    document = {
        "_id": tasks.EVALUATION_LOCK_ID,
        "active_task_id": "",
        "last_started_at": None,
        "created_at": now,
    }
    document.update(overrides)
    return document


def authenticated_client(testcase, user, *, enforce_csrf: bool = True):
    client = Client(enforce_csrf_checks=enforce_csrf)
    session = client.session
    session["user_id"] = str(user["_id"])
    session.save()
    client.cookies["csrftoken"] = CSRF_TOKEN
    stack = ExitStack()
    testcase.addCleanup(stack.close)
    stack.enter_context(patch("apps.users.decorators.get_active_user", return_value=user))
    stack.enter_context(patch("apps.courses.views.get_active_user", return_value=user))
    return client


# ---------------------------------------------------------------------------
# Evaluation core: real run, atomic writes, failure preservation
# ---------------------------------------------------------------------------

class TestEvaluationCore(MongoTestCase):
    def setUp(self):
        super().setUp()
        ai.clear_ai_cache()
        self.enterContext(groq_env())
        self.api_mock = self.enterContext(
            patch("apps.courses.ai.call_groq_api", return_value=VALID_META_JSON)
        )
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.markdown, self.structured = self.enterContext(
            report_paths(self.directory.name)
        )

    def _no_temp_files(self):
        return [p for p in Path(self.directory.name).iterdir() if p.suffix == ".tmp"]

    def test_success_writes_real_reports_atomically(self):
        progress = []
        result = run_evaluation(
            progress_callback=lambda done, total: progress.append((done, total)),
            delay=0,
        )

        self.assertEqual(result.outcome.answered, 36)
        self.assertEqual(progress, [(index, 36) for index in range(1, 37)])
        payload = json.loads(self.structured.read_text(encoding="utf-8"))
        self.assertEqual(payload["mode"], "real")
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["coverage"]["answered"], 36)
        self.assertEqual(payload["coverage"]["total"], 36)
        self.assertTrue(payload["generated_at"])
        self.assertIn("évaluation réelle", self.markdown.read_text(encoding="utf-8"))
        self.assertEqual(self._no_temp_files(), [])

    def test_sleep_is_called_between_entries_with_configured_delay(self):
        import time as _real_time
        def _safe_sleep(s):
            if s != 2.5:
                _real_time.sleep(s)
        with patch("apps.courses.management.commands.ai_report.time.sleep", side_effect=_safe_sleep) as sleep_mock:
            run_evaluation(delay=2.5)
        delays = [c for c in sleep_mock.call_args_list if c == call(2.5)]
        self.assertEqual(len(delays), 35)

    def test_missing_key_raises_and_keeps_previous_reports(self):
        self.markdown.write_text("ancien rapport", encoding="utf-8")
        self.structured.write_text('{"mode": "real", "ancien": true}', encoding="utf-8")

        with groq_env(key=""):
            with self.assertRaises(EvaluationNotConfiguredError) as raised:
                run_evaluation(delay=0)

        message = str(raised.exception)
        self.assertIn("GROQ_API_KEY", message)
        self.assertIn("aucun mode simulé", message)
        self.api_mock.assert_not_called()
        self.assertEqual(self.markdown.read_text(encoding="utf-8"), "ancien rapport")
        self.assertIn("ancien", self.structured.read_text(encoding="utf-8"))

    def test_evaluation_error_keeps_previous_reports(self):
        self.markdown.write_text("ancien rapport", encoding="utf-8")
        self.structured.write_text('{"ancien": true}', encoding="utf-8")

        with patch.object(ai_report, "_run_eval", side_effect=EvaluationError("échec")):
            with self.assertRaises(EvaluationError):
                run_evaluation(delay=0)

        self.assertEqual(self.markdown.read_text(encoding="utf-8"), "ancien rapport")
        self.assertEqual(
            self.structured.read_text(encoding="utf-8"), '{"ancien": true}'
        )

    def test_unexpected_entry_failure_keeps_previous_reports(self):
        self.markdown.write_text("ancien rapport", encoding="utf-8")
        self.structured.write_text('{"ancien": true}', encoding="utf-8")

        with patch.object(
            ai_report,
            "suggest_meta_for_evaluation",
            side_effect=RuntimeError("panne"),
        ):
            with self.assertRaises(RuntimeError):
                run_evaluation(delay=0)

        self.assertEqual(self.markdown.read_text(encoding="utf-8"), "ancien rapport")
        self.assertEqual(
            self.structured.read_text(encoding="utf-8"), '{"ancien": true}'
        )

    def test_zero_answered_is_a_failure_when_answers_are_required(self):
        self.markdown.write_text("ancien rapport", encoding="utf-8")
        self.structured.write_text('{"ancien": true}', encoding="utf-8")
        outcome = ai_report.EvalOutcome(
            cat_true=[],
            cat_pred=[],
            lvl_true=[],
            lvl_pred=[],
            latencies_ms=[],
            answered=0,
            rate_limit_errors=0,
            timeout_errors=0,
            other_api_errors=36,
            invalid_model_errors=0,
            total=36,
        )

        with patch.object(ai_report, "_run_eval", return_value=outcome):
            with self.assertRaises(EvaluationError) as raised:
                run_evaluation(delay=0, require_answers=True)

        self.assertIn("rapport précédent est conservé", str(raised.exception))
        self.assertEqual(self.markdown.read_text(encoding="utf-8"), "ancien rapport")
        self.assertEqual(
            self.structured.read_text(encoding="utf-8"), '{"ancien": true}'
        )

    def test_atomic_write_replaces_target_and_removes_temp_file(self):
        target = Path(self.directory.name) / "atomic.txt"
        target.write_text("ancien", encoding="utf-8")

        _atomic_write_text(target, "nouveau")

        self.assertEqual(target.read_text(encoding="utf-8"), "nouveau")
        self.assertEqual(self._no_temp_files(), [])

    def test_atomic_write_preserves_old_file_when_replace_fails(self):
        target = Path(self.directory.name) / "atomic.txt"
        target.write_text("ancien", encoding="utf-8")

        with patch.object(
            ai_report.os, "replace", side_effect=OSError("disque plein")
        ):
            with self.assertRaises(OSError):
                _atomic_write_text(target, "nouveau")

        self.assertEqual(target.read_text(encoding="utf-8"), "ancien")
        self.assertEqual(self._no_temp_files(), [])

    def test_report_write_failure_is_non_fatal(self):
        with patch.object(
            ai_report, "_atomic_write_text", side_effect=OSError("lecture seule")
        ):
            result = run_evaluation(delay=0)
        self.assertEqual(result.outcome.answered, 36)


# ---------------------------------------------------------------------------
# Persisted status contract
# ---------------------------------------------------------------------------

class TestEvaluationDispatch(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(
            setattr, tasks, "LAST_DISPATCH_MODE", tasks.LAST_DISPATCH_MODE
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_celery_is_used_when_async_mode_is_enabled(self):
        with patch.object(
            tasks.run_ai_evaluation_task, "apply_async"
        ) as apply_async:
            tasks._dispatch_evaluation_task("eval-1", 0)

        apply_async.assert_called_once_with(args=("eval-1", 0), task_id="eval-1")
        self.assertEqual(tasks.get_ai_execution_mode(), "celery")

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_broker_failure_falls_back_to_thread(self):
        with patch.object(
            tasks.run_ai_evaluation_task,
            "apply_async",
            side_effect=RuntimeError("broker down"),
        ), patch.object(tasks, "_evaluation_thread_worker") as worker:
            tasks._dispatch_evaluation_task("eval-2", 0)

        self.assertEqual(tasks.get_ai_execution_mode(), "thread")
        worker.assert_called_once_with("eval-2", 0)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_eager_mode_uses_thread_without_touching_the_broker(self):
        with patch.object(
            tasks.run_ai_evaluation_task, "apply_async"
        ) as apply_async, patch.object(
            tasks, "_evaluation_thread_worker"
        ) as worker:
            tasks._dispatch_evaluation_task("eval-3", 0)

        apply_async.assert_not_called()
        self.assertEqual(tasks.get_ai_execution_mode(), "thread")
        worker.assert_called_once_with("eval-3", 0)


class TestEvaluationStatus(MongoTestCase):
    EXPECTED_KEYS = {"state", "done", "total", "error_code"}

    def test_states_match_the_contract(self):
        self.assertEqual(
            tasks.EVALUATION_STATUSES,
            ("pending", "running", "done", "error"),
        )

    def test_unknown_task_returns_error_contract(self):
        data = tasks.get_ai_evaluation_status("eval-inconnu")
        self.assertEqual(set(data), self.EXPECTED_KEYS)
        self.assertEqual(data["state"], "error")
        self.assertEqual(data["error_code"], "unknown_task")
        self.assertEqual(data["done"], 0)
        self.assertEqual(data["total"], 0)

    def test_running_task_exposes_progress(self):
        self.ai_tasks_collection.insert_one(
            make_eval_task(status="running", done=12, total=36)
        )
        data = tasks.get_ai_evaluation_status("eval-test")
        self.assertEqual(
            data,
            {"state": "running", "done": 12, "total": 36, "error_code": ""},
        )

    def test_done_state_forces_done_to_total(self):
        self.ai_tasks_collection.insert_one(
            make_eval_task(status="done", done=3, total=36)
        )
        data = tasks.get_ai_evaluation_status("eval-test")
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["done"], 36)
        self.assertEqual(data["error_code"], "")

    def test_invalid_status_is_reported_as_error(self):
        self.ai_tasks_collection.insert_one(
            make_eval_task(status="inconnu", total=36)
        )
        data = tasks.get_ai_evaluation_status("eval-test")
        self.assertEqual(data["state"], "error")
        self.assertEqual(data["error_code"], "internal_error")

    def test_storage_failure_is_reported(self):
        collection = MagicMock()
        collection.find_one.side_effect = PyMongoError("mongo down")
        with patch.object(tasks, "ai_tasks_collection", collection):
            data = tasks.get_ai_evaluation_status("eval-test")
        self.assertEqual(data["state"], "error")
        self.assertEqual(data["error_code"], "storage_error")


# ---------------------------------------------------------------------------
# Start safeguards: key, dataset, lock, cooldown, dispatch
# ---------------------------------------------------------------------------

class TestStartEvaluationSafeguards(MongoTestCase):
    def setUp(self):
        super().setUp()
        ai.clear_ai_cache()
        self.enterContext(groq_env())
        self.dispatch = self.enterContext(
            patch.object(tasks, "_dispatch_evaluation_task")
        )

    def _evaluation_tasks(self):
        return list(
            self.ai_tasks_collection.find({"kind": tasks.EVALUATION_TASK_KIND})
        )

    def _lock(self):
        return self.ai_tasks_collection.find_one({"_id": tasks.EVALUATION_LOCK_ID}) or {}

    def test_missing_key_raises_without_dispatch_or_task(self):
        with groq_env(key=""):
            with self.assertRaises(tasks.AIEvaluationNotConfiguredError) as raised:
                tasks.start_ai_evaluation(delay=0)

        message = str(raised.exception)
        self.assertIn("GROQ_API_KEY", message)
        self.assertIn("aucun mode simulé", message)
        self.dispatch.assert_not_called()
        self.assertEqual(self._evaluation_tasks(), [])
        self.assertIsNone(
            self.ai_tasks_collection.find_one({"_id": tasks.EVALUATION_LOCK_ID})
        )

    def test_invalid_dataset_raises_french_error(self):
        with patch.object(
            tasks, "_load_dataset", side_effect=EvaluationDatasetError("illisible")
        ):
            with self.assertRaises(tasks.AIEvaluationDatasetError) as raised:
                tasks.start_ai_evaluation(delay=0)
        self.assertIn("Aucune évaluation n'a été lancée", str(raised.exception))
        self.dispatch.assert_not_called()
        self.assertEqual(self._evaluation_tasks(), [])

    def test_success_persists_pending_task_and_claims_lock(self):
        task_id = tasks.start_ai_evaluation(delay=0)

        self.assertTrue(task_id.startswith("eval-"))
        document = self.ai_tasks_collection.find_one({"_id": task_id})
        self.assertEqual(document["kind"], tasks.EVALUATION_TASK_KIND)
        self.assertEqual(document["status"], "pending")
        self.assertEqual(document["done"], 0)
        self.assertEqual(document["total"], 36)
        self.assertEqual(self._lock()["active_task_id"], task_id)
        self.assertIsNotNone(self._lock()["last_started_at"])
        self.dispatch.assert_called_once()
        self.assertEqual(self.dispatch.call_args.args[0], task_id)

    def test_concurrent_evaluation_is_rejected(self):
        for status in ("pending", "running"):
            with self.subTest(status=status):
                self.ai_tasks_collection.delete_many({})
                self.ai_tasks_collection.insert_one(
                    make_eval_task(_id="eval-active", status=status)
                )
                self.ai_tasks_collection.insert_one(
                    make_lock(active_task_id="eval-active")
                )
                with self.assertRaises(tasks.AIEvaluationAlreadyRunningError) as raised:
                    tasks.start_ai_evaluation(delay=0)
                self.assertIn("déjà en cours", str(raised.exception))
                self.assertEqual(self._evaluation_tasks()[0]["_id"], "eval-active")

    def test_stale_active_task_is_cleared(self):
        self.ai_tasks_collection.insert_one(
            make_eval_task(_id="eval-old", status="done")
        )
        self.ai_tasks_collection.insert_one(make_lock(active_task_id="eval-old"))

        task_id = tasks.start_ai_evaluation(delay=0)

        self.assertNotEqual(task_id, "eval-old")
        self.assertEqual(self._lock()["active_task_id"], task_id)

    def test_active_task_without_document_is_cleared(self):
        self.ai_tasks_collection.insert_one(
            make_lock(active_task_id="eval-disparu")
        )

        task_id = tasks.start_ai_evaluation(delay=0)

        self.assertEqual(self._lock()["active_task_id"], task_id)

    def test_cooldown_rejects_with_remaining_time(self):
        started = datetime.now(timezone.utc) - timedelta(seconds=120)
        self.ai_tasks_collection.insert_one(
            make_lock(last_started_at=started)
        )

        with self.assertRaises(tasks.AIEvaluationCooldownError) as raised:
            tasks.start_ai_evaluation(delay=0)

        error = raised.exception
        self.assertGreaterEqual(error.remaining_seconds, 470)
        self.assertLessEqual(error.remaining_seconds, 481)
        self.assertIn("10 minutes", str(error))
        self.assertIn("min", str(error))
        self.assertEqual(self._evaluation_tasks(), [])

    def test_claim_storage_failure_returns_dispatch_error(self):
        with patch.object(
            tasks,
            "_claim_evaluation_slot",
            side_effect=PyMongoError("mongo down"),
        ):
            with self.assertRaises(tasks.AIEvaluationDispatchError):
                tasks.start_ai_evaluation(delay=0)

        self.dispatch.assert_not_called()
        self.assertEqual(self._evaluation_tasks(), [])

    def test_task_insert_failure_releases_lock(self):
        with patch.object(
            tasks.ai_tasks_collection,
            "insert_one",
            side_effect=PyMongoError("insert failed"),
        ):
            with self.assertRaises(tasks.AIEvaluationDispatchError) as raised:
                tasks.start_ai_evaluation(delay=0)

        self.assertIn("Impossible de démarrer", str(raised.exception))
        self.assertEqual(self._lock().get("active_task_id"), "")
        self.assertIsNone(self._lock().get("last_started_at"))
        self.assertEqual(self._evaluation_tasks(), [])

    def test_dispatch_failure_removes_task_and_releases_lock(self):
        with patch.object(
            tasks,
            "_dispatch_evaluation_task",
            side_effect=RuntimeError("broker down"),
        ):
            with self.assertRaises(tasks.AIEvaluationDispatchError):
                tasks.start_ai_evaluation(delay=0)

        self.assertEqual(self._evaluation_tasks(), [])
        self.assertEqual(self._lock().get("active_task_id"), "")
        self.assertIsNone(self._lock().get("last_started_at"))


# ---------------------------------------------------------------------------
# Background task execution
# ---------------------------------------------------------------------------

@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class TestEvaluationTaskExecution(MongoTestCase):
    def setUp(self):
        super().setUp()
        ai.clear_ai_cache()
        self.enterContext(groq_env())
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.markdown, self.structured = self.enterContext(
            report_paths(self.directory.name)
        )
        self.task_id = "eval-task"
        self.ai_tasks_collection.insert_one(make_eval_task(_id=self.task_id))
        self.ai_tasks_collection.insert_one(
            make_lock(active_task_id=self.task_id)
        )

    def _document(self):
        return self.ai_tasks_collection.find_one({"_id": self.task_id})

    def _lock(self):
        return self.ai_tasks_collection.find_one({"_id": tasks.EVALUATION_LOCK_ID}) or {}

    def test_success_updates_progress_then_marks_done(self):
        captured = {}

        def fake_run(
            progress_callback=None,
            delay=2.5,
            *,
            stdout=None,
            require_answers=False,
        ):
            self.assertTrue(require_answers)
            progress_callback(12, 36)
            captured["mid"] = self._document()
            progress_callback(36, 36)
            return SimpleNamespace(
                outcome=SimpleNamespace(total=36, answered=36)
            )

        with patch.object(tasks, "run_evaluation", side_effect=fake_run):
            result = tasks._execute_ai_evaluation(self.task_id, 0)

        self.assertEqual(captured["mid"]["status"], "running")
        self.assertEqual(captured["mid"]["done"], 12)
        self.assertEqual(captured["mid"]["total"], 36)
        self.assertTrue(result["ok"])
        document = self._document()
        self.assertEqual(document["status"], "done")
        self.assertEqual(document["done"], 36)
        self.assertEqual(document["total"], 36)
        self.assertEqual(document["error_code"], "")
        self.assertIn("finished_at", document)
        self.assertEqual(document["result"]["answered"], 36)
        self.assertEqual(self._lock().get("active_task_id"), "")

    def test_missing_key_marks_error_and_keeps_reports(self):
        self.markdown.write_text("ancien rapport", encoding="utf-8")
        self.structured.write_text('{"ancien": true}', encoding="utf-8")

        with groq_env(key=""):
            result = tasks._execute_ai_evaluation(self.task_id, 0)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "missing_key")
        document = self._document()
        self.assertEqual(document["status"], "error")
        self.assertEqual(document["error_code"], "missing_key")
        self.assertIn("GROQ_API_KEY", document["error"])
        self.assertEqual(self.markdown.read_text(encoding="utf-8"), "ancien rapport")
        self.assertEqual(
            self.structured.read_text(encoding="utf-8"), '{"ancien": true}'
        )
        self.assertEqual(self._lock().get("active_task_id"), "")

    def test_dataset_error_is_mapped(self):
        with patch.object(
            tasks,
            "run_evaluation",
            side_effect=EvaluationDatasetError("jeu illisible"),
        ):
            result = tasks._execute_ai_evaluation(self.task_id, 0)

        self.assertEqual(result["error_code"], "invalid_dataset")
        document = self._document()
        self.assertEqual(document["status"], "error")
        self.assertEqual(document["error_code"], "invalid_dataset")

    def test_thread_worker_marks_generic_error_when_execution_crashes(self):
        with patch.object(
            tasks,
            "_execute_ai_evaluation",
            side_effect=RuntimeError("crash"),
        ):
            tasks._evaluation_thread_worker(self.task_id, 0)

        document = self._document()
        self.assertEqual(document["status"], "error")
        self.assertEqual(document["error_code"], "internal_error")
        self.assertIn("rapport précédent est conservé", document["error"])
        self.assertEqual(self._lock().get("active_task_id"), "")

    def test_zero_answers_marks_error_and_keeps_previous_report(self):
        self.markdown.write_text("ancien rapport", encoding="utf-8")
        self.structured.write_text('{"ancien": true}', encoding="utf-8")

        with patch.object(
            ai_report,
            "suggest_meta_for_evaluation",
            side_effect=ai.AIProviderError("indisponible", status_code=500),
        ):
            result = tasks._execute_ai_evaluation(self.task_id, 0)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "evaluation_error")
        self.assertIn("rapport précédent est conservé", result["error"])
        self.assertEqual(self.markdown.read_text(encoding="utf-8"), "ancien rapport")
        self.assertEqual(
            self.structured.read_text(encoding="utf-8"), '{"ancien": true}'
        )
        self.assertEqual(self._lock().get("active_task_id"), "")

    def test_unexpected_failure_is_generic_and_keeps_previous_report(self):
        self.markdown.write_text("ancien rapport", encoding="utf-8")
        self.structured.write_text('{"ancien": true}', encoding="utf-8")

        with patch.object(
            ai_report,
            "suggest_meta_for_evaluation",
            side_effect=RuntimeError("panne inattendue"),
        ):
            result = tasks._execute_ai_evaluation(self.task_id, 0)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "internal_error")
        self.assertIn("rapport précédent est conservé", result["error"])
        document = self._document()
        self.assertEqual(document["status"], "error")
        self.assertEqual(document["error_code"], "internal_error")
        self.assertNotIn("panne inattendue", document["error"])
        self.assertEqual(self.markdown.read_text(encoding="utf-8"), "ancien rapport")
        self.assertEqual(
            self.structured.read_text(encoding="utf-8"), '{"ancien": true}'
        )
        self.assertEqual(self._lock().get("active_task_id"), "")


# ---------------------------------------------------------------------------
# Route permissions, CSRF and HTTP error mapping
# ---------------------------------------------------------------------------

@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class TestReportRunPermissions(MongoTestCase):
    def setUp(self):
        super().setUp()
        ai.clear_ai_cache()
        self.admin = user_document(role="admin", email="admin-run@example.com")
        self.instructor = user_document(role="instructor", email="instr-run@example.com")
        self.student = user_document(role="student", email="student-run@example.com")

    def _run_url(self, namespace):
        return reverse(f"{namespace}:course_manage_ai_report_run")

    def _status_url(self, namespace, task_id="eval-test"):
        return reverse(
            f"{namespace}:course_manage_ai_report_status", args=[task_id]
        )

    def test_routes_are_available_in_both_namespaces(self):
        self.assertEqual(
            reverse("courses:course_manage_ai_report_run"),
            "/backoffice/cours/manage/ai/report/run/",
        )
        self.assertEqual(
            reverse("users:course_manage_ai_report_run"),
            "/users/backoffice/cours/manage/ai/report/run/",
        )
        self.assertEqual(
            reverse(
                "users:course_manage_ai_report_status", args=["eval-test"]
            ),
            "/users/backoffice/cours/manage/ai/report/status/eval-test/",
        )

    def test_anonymous_is_redirected_on_run_and_status(self):
        for namespace in NAMESPACES:
            with self.subTest(namespace=namespace):
                run_response = self.client.post(
                    self._run_url(namespace),
                    {},
                    HTTP_X_CSRFTOKEN=CSRF_TOKEN,
                )
                self.assertIn(run_response.status_code, [302, 403])
                status_response = self.client.get(self._status_url(namespace))
                self.assertIn(status_response.status_code, [302, 403])

    def test_student_is_forbidden_on_run_and_status(self):
        client = authenticated_client(self, self.student)
        for namespace in NAMESPACES:
            with self.subTest(namespace=namespace):
                run_response = client.post(
                    self._run_url(namespace),
                    {},
                    HTTP_X_CSRFTOKEN=CSRF_TOKEN,
                )
                self.assertEqual(run_response.status_code, 403)
                status_response = client.get(self._status_url(namespace))
                self.assertEqual(status_response.status_code, 403)

    def test_instructor_is_forbidden_on_run_and_status(self):
        client = authenticated_client(self, self.instructor)
        for namespace in NAMESPACES:
            with self.subTest(namespace=namespace):
                run_response = client.post(
                    self._run_url(namespace),
                    {},
                    HTTP_X_CSRFTOKEN=CSRF_TOKEN,
                )
                self.assertEqual(run_response.status_code, 403)
                status_response = client.get(self._status_url(namespace))
                self.assertEqual(status_response.status_code, 403)

    def test_admin_can_start_in_both_namespaces(self):
        client = authenticated_client(self, self.admin)
        for namespace in NAMESPACES:
            with self.subTest(namespace=namespace):
                with patch(
                    "apps.courses.views.start_ai_evaluation",
                    return_value="eval-test",
                ):
                    response = client.post(
                        self._run_url(namespace),
                        {},
                        HTTP_X_CSRFTOKEN=CSRF_TOKEN,
                    )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"task_id": "eval-test"})

    def test_admin_can_read_status_with_exact_contract(self):
        self.ai_tasks_collection.insert_one(
            make_eval_task(status="running", done=12, total=36)
        )
        client = authenticated_client(self, self.admin)
        for namespace in NAMESPACES:
            with self.subTest(namespace=namespace):
                response = client.get(self._status_url(namespace))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {
                        "state": "running",
                        "done": 12,
                        "total": 36,
                        "error_code": "",
                    },
                )

    def test_csrf_is_enforced_on_run(self):
        client = authenticated_client(self, self.admin)
        response = client.post(self._run_url("users"), {})
        self.assertEqual(response.status_code, 403)

    def test_get_is_not_allowed_on_run(self):
        client = authenticated_client(self, self.admin)
        for namespace in NAMESPACES:
            with self.subTest(namespace=namespace):
                response = client.get(self._run_url(namespace))
                self.assertEqual(response.status_code, 405)

    def test_post_is_not_allowed_on_status(self):
        client = authenticated_client(self, self.admin)
        response = client.post(
            self._status_url("users"), {}, HTTP_X_CSRFTOKEN=CSRF_TOKEN
        )
        self.assertEqual(response.status_code, 405)

    def test_oversized_task_id_returns_400(self):
        client = authenticated_client(self, self.admin)
        response = client.get(self._status_url("users", "x" * 101))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error_code"], "invalid_task_id")

    def test_unknown_task_returns_error_state(self):
        client = authenticated_client(self, self.admin)
        response = client.get(self._status_url("users", "eval-unknown"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error_code"], "unknown_task")

    def test_missing_key_returns_503_without_mock_fallback(self):
        client = authenticated_client(self, self.admin)
        error = tasks.AIEvaluationNotConfiguredError(
            "La clé GROQ_API_KEY n'est pas configurée ; aucun mode simulé n'est utilisé."
        )
        with patch("apps.courses.views.start_ai_evaluation", side_effect=error):
            response = client.post(
                self._run_url("users"), {}, HTTP_X_CSRFTOKEN=CSRF_TOKEN
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("GROQ_API_KEY", response.json()["error"])
        self.assertIn("aucun mode simulé", response.json()["error"])

    def test_concurrent_run_returns_409(self):
        client = authenticated_client(self, self.admin)
        error = tasks.AIEvaluationAlreadyRunningError(
            "Une évaluation est déjà en cours. Attendez sa fin avant de la relancer."
        )
        with patch("apps.courses.views.start_ai_evaluation", side_effect=error):
            response = client.post(
                self._run_url("users"), {}, HTTP_X_CSRFTOKEN=CSRF_TOKEN
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("déjà en cours", response.json()["error"])

    def test_cooldown_returns_429_with_remaining_time(self):
        client = authenticated_client(self, self.admin)
        with patch(
            "apps.courses.views.start_ai_evaluation",
            side_effect=tasks.AIEvaluationCooldownError(480),
        ):
            response = client.post(
                self._run_url("users"), {}, HTTP_X_CSRFTOKEN=CSRF_TOKEN
            )
        self.assertEqual(response.status_code, 429)
        payload = response.json()
        self.assertEqual(payload["remaining_seconds"], 480)
        self.assertIn("8 min 00 s", payload["error"])
        self.assertIn("10 minutes", payload["error"])

    def test_dispatch_failure_returns_503(self):
        client = authenticated_client(self, self.admin)
        with patch(
            "apps.courses.views.start_ai_evaluation",
            side_effect=tasks.AIEvaluationDispatchError("échec du dispatch"),
        ):
            response = client.post(
                self._run_url("users"), {}, HTTP_X_CSRFTOKEN=CSRF_TOKEN
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("échec du dispatch", response.json()["error"])

    def test_unexpected_error_returns_generic_503(self):
        client = authenticated_client(self, self.admin)
        with patch(
            "apps.courses.views.start_ai_evaluation",
            side_effect=RuntimeError("boom"),
        ):
            response = client.post(
                self._run_url("users"), {}, HTTP_X_CSRFTOKEN=CSRF_TOKEN
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("boom", response.json()["error"])


# ---------------------------------------------------------------------------
# End-to-end: view → background task → status polling → report refresh
# ---------------------------------------------------------------------------

@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class TestReportRunEndToEnd(MongoTestCase):
    def setUp(self):
        super().setUp()
        ai.clear_ai_cache()
        self.admin = user_document(role="admin", email="admin-e2e@example.com")
        self.client = authenticated_client(self, self.admin)

    def _wait_for_completion(self, client, task_id, timeout=10.0):
        url = reverse("users:course_manage_ai_report_status", args=[task_id])
        deadline = time.time() + timeout
        payload = {"state": "pending"}
        while time.time() < deadline:
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            if payload["state"] in {"done", "error"}:
                return payload
            time.sleep(0.02)
        return payload

    def test_run_completes_writes_report_and_enforces_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            with report_paths(directory) as (markdown, structured):
                with groq_env(), patch(
                    "apps.courses.ai.call_groq_api",
                    return_value=VALID_META_JSON,
                ), patch(
                    "apps.courses.views.start_ai_evaluation",
                    side_effect=lambda: tasks.start_ai_evaluation(delay=0),
                ):
                    response = self.client.post(
                        reverse("users:course_manage_ai_report_run"),
                        {},
                        HTTP_X_CSRFTOKEN=CSRF_TOKEN,
                    )
                    self.assertEqual(response.status_code, 200)
                    task_id = response.json()["task_id"]

                    payload = self._wait_for_completion(self.client, task_id)
                    self.assertEqual(
                        payload,
                        {
                            "state": "done",
                            "done": 36,
                            "total": 36,
                            "error_code": "",
                        },
                    )

                    report = json.loads(structured.read_text(encoding="utf-8"))
                    self.assertEqual(report["mode"], "real")
                    self.assertEqual(report["coverage"]["answered"], 36)
                    self.assertTrue(markdown.exists())

                    dataset = json.loads(
                        ai_report._DATASET_PATH.read_text(encoding="utf-8")
                    )
                    sample_title = dataset[0]["title"]
                    serialized_logs = json.dumps(
                        list(self.ai_collection.find({})),
                        ensure_ascii=False,
                        default=str,
                    )
                    serialized_task = json.dumps(
                        self.ai_tasks_collection.find_one({"_id": task_id}),
                        ensure_ascii=False,
                        default=str,
                    )
                    self.assertNotIn(sample_title, serialized_logs)
                    self.assertNotIn(sample_title, serialized_task)
                    self.assertNotIn("test-key", serialized_logs)
                    self.assertNotIn("test-key", serialized_task)

                    second = self.client.post(
                        reverse("users:course_manage_ai_report_run"),
                        {},
                        HTTP_X_CSRFTOKEN=CSRF_TOKEN,
                    )
                    self.assertEqual(second.status_code, 429)
                    self.assertIn("remaining_seconds", second.json())


# ---------------------------------------------------------------------------
# Report page visibility and static assets
# ---------------------------------------------------------------------------

@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class TestReportRunPage(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.admin = user_document(role="admin", email="admin-page@example.com")
        self.instructor = user_document(role="instructor", email="instr-page@example.com")
        self.student = user_document(role="student", email="student-page@example.com")

    def test_admin_sees_run_controls(self):
        client = authenticated_client(self, self.admin, enforce_csrf=False)
        with report_file(report_payload()):
            for namespace in NAMESPACES:
                with self.subTest(namespace=namespace):
                    response = client.get(
                        reverse(f"{namespace}:course_manage_ai_report")
                    )
                    self.assertEqual(response.status_code, 200)
                    html = response.content.decode()
                    self.assertIn("ai-report-run-button", html)
                    self.assertIn("Rerun evaluation", html)
                    self.assertIn("Confirm launch", html)
                    self.assertIn("2.5 seconds", html)
                    self.assertIn("10 minutes", html)
                    self.assertIn("36 cours", html)
                    self.assertIn("ai-report-run-progress-bar", html)
                    self.assertIn('aria-live="polite"', html)
                    self.assertIn('role="alert"', html)
                    self.assertIn("courses-ai-report.js", html)
                    self.assertIn("csrfmiddlewaretoken", html)
                    self.assertIn("data-run-url=", html)
                    self.assertIn("data-status-url=", html)
                    self.assertIn("report/status/__TASK__/", html)

    def test_controls_are_hidden_for_instructors(self):
        client = authenticated_client(self, self.instructor)
        with report_file(report_payload()):
            response = client.get(reverse("users:course_manage_ai_report"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("ai-report-run-button", html)
        self.assertNotIn("Rerun evaluation", html)
        self.assertNotIn("courses-ai-report.js", html)
        self.assertIn("openai/gpt-oss-20b", html)

    def test_student_is_forbidden(self):
        client = authenticated_client(self, self.student)
        with report_file(report_payload()):
            response = client.get(reverse("users:course_manage_ai_report"))
        self.assertEqual(response.status_code, 403)

    def test_admin_sees_run_controls_without_existing_report(self):
        client = authenticated_client(self, self.admin, enforce_csrf=False)
        with report_file():
            response = client.get(reverse("users:course_manage_ai_report"))
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("No AI report available", html)
        self.assertIn("ai-report-run-button", html)

    def test_confirmation_and_progress_start_hidden(self):
        client = authenticated_client(self, self.admin, enforce_csrf=False)
        with report_file(report_payload()):
            response = client.get(reverse("users:course_manage_ai_report"))
        html = response.content.decode()
        self.assertRegex(
            html,
            r'id="ai-report-run-confirmation"[^>]*hidden',
        )
        self.assertRegex(
            html,
            r'id="ai-report-run-progress"[^>]*hidden',
        )
        self.assertRegex(
            html,
            r'id="ai-report-run-error"[^>]*hidden',
        )


class TestReportRunAssets(SimpleTestCase):
    def _static(self, name):
        return (Path(settings.BASE_DIR) / "static" / "backoffice" / name).read_text(
            encoding="utf-8"
        )

    def test_javascript_uses_text_content_and_required_requests(self):
        source = self._static("courses-ai-report.js")
        self.assertNotIn("innerHTML", source)
        self.assertIn("textContent", source)
        self.assertIn('method: "GET"', source)
        self.assertIn('method: "POST"', source)
        self.assertIn("window.location.reload()", source)
        self.assertIn("window.setTimeout", source)
        self.assertIn("runButton.disabled = busy", source)
        self.assertIn('setAttribute("aria-busy"', source)
        self.assertIn('"Évaluation en cours : "', source)
        self.assertIn("confirmButton.focus()", source)
        self.assertIn("ai-report-run-progress-bar", source)
        self.assertIn("ai-report-run-error", source)

    def test_css_respects_hidden_and_touch_targets(self):
        source = self._static("courses.css")
        self.assertIn("[hidden]", source)
        self.assertIn("display: none !important", source)
        self.assertIn("min-height: 2.75rem", source)
        self.assertIn(".ai-report-run-progress", source)
