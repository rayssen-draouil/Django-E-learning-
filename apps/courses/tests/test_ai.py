import json
import socket
import urllib.error
from contextlib import ExitStack
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from apps.courses import ai

from .base import MongoTestCase, user_document


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def api_payload(description="Description générée.", objectives=None):
    objectives = objectives or [f"Objectif {index}" for index in range(1, 6)]
    return {"content": [{"type": "text", "text": json.dumps({
        "description": description,
        "objectives": objectives,
    })}]}


@override_settings(
    SESSION_ENGINE="apps.users.tests.session_backend",
    ANTHROPIC_API_KEY="test-key",
    AI_MODEL="test-model",
    AI_TIMEOUT=2,
)
class CourseAITests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.instructor = user_document(role="instructor", email="ai@example.com")
        self.student = user_document(role="student", email="student-ai@example.com")

    def _authenticate(self, user):
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session["user_id"] = str(user["_id"])
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("apps.users.decorators.get_active_user", return_value=user))
        stack.enter_context(patch("apps.courses.views.get_active_user", return_value=user))
        return client

    def _token(self, client):
        token = get_token(RequestFactory().get("/"))
        client.cookies["csrftoken"] = token
        return token

    def test_generate_requires_a_manager_and_post(self):
        self.assertEqual(
            self.client.post(reverse("users:course_manage_ai_generate")).status_code,
            302,
        )
        student = self._authenticate(self.student)
        token = self._token(student)
        response = student.post(
            reverse("users:course_manage_ai_generate"),
            {"title": "Cours valide", "level": "beginner"},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 403)

    def test_generate_validates_title_and_level(self):
        client = self._authenticate(self.instructor)
        token = self._token(client)
        url = reverse("users:course_manage_ai_generate")
        self.assertEqual(client.post(url, {"title": "ab", "level": "beginner"}, HTTP_X_CSRFTOKEN=token).status_code, 400)
        self.assertEqual(client.post(url, {"title": "Cours valide", "level": "expert"}, HTTP_X_CSRFTOKEN=token).status_code, 400)

    def test_generate_returns_task_id_and_status_returns_content(self):
        client = self._authenticate(self.instructor)
        token = self._token(client)
        generated = {
            "description": "Une description générée.",
            "objectives": [f"Objectif {index}" for index in range(1, 6)],
        }
        with patch("apps.courses.tasks.generate_course_content", return_value=generated):
            response = client.post(
                reverse("users:course_manage_ai_generate"),
                {"title": "Cours IA", "level": "beginner"},
                HTTP_X_CSRFTOKEN=token,
            )
        self.assertEqual(response.status_code, 200)
        task_id = response.json()["task_id"]

        status = client.get(reverse("users:course_manage_ai_status", args=[task_id]))
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "done")
        self.assertEqual(status.json()["description"], generated["description"])
        self.assertEqual(status.json()["objectives"], generated["objectives"])
        self.assertEqual(self.ai_collection.count_documents({}), 1)

    def test_generate_reports_an_unavailable_service(self):
        client = self._authenticate(self.instructor)
        token = self._token(client)
        with patch("apps.courses.views.generate_course_content_task.delay", side_effect=RuntimeError):
            response = client.post(
                reverse("users:course_manage_ai_generate"),
                {"title": "Cours IA", "level": "beginner"},
                HTTP_X_CSRFTOKEN=token,
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("indisponible", response.json()["error"])

    def test_status_reports_a_failed_task(self):
        client = self._authenticate(self.instructor)
        token = self._token(client)
        with patch(
            "apps.courses.tasks.generate_course_content",
            side_effect=ai.AIError("Quota dépassé."),
        ):
            response = client.post(
                reverse("users:course_manage_ai_generate"),
                {"title": "Cours IA", "level": "beginner"},
                HTTP_X_CSRFTOKEN=token,
            )
        task_id = response.json()["task_id"]
        status = client.get(reverse("users:course_manage_ai_status", args=[task_id]))
        self.assertEqual(status.json()["status"], "error")
        self.assertIn("Quota", status.json()["error"])


class AIContentTests(MongoTestCase):
    @override_settings(ANTHROPIC_API_KEY="")
    def test_generate_requires_an_api_key(self):
        with self.assertRaisesMessage(ai.AIError, "ANTHROPIC_API_KEY"):
            ai.generate_course_content("Cours", "beginner")

    @override_settings(ANTHROPIC_API_KEY="test-key", AI_TIMEOUT=2)
    def test_generate_parses_a_valid_api_response(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse(api_payload())):
            content = ai.generate_course_content("Cours", "beginner")
        self.assertEqual(content["description"], "Description générée.")
        self.assertEqual(len(content["objectives"]), 5)

    @override_settings(ANTHROPIC_API_KEY="test-key", AI_TIMEOUT=2)
    def test_generate_handles_api_errors_and_timeouts(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("url", 429, "rate", {}, None),
        ):
            with self.assertRaisesMessage(ai.AIError, "Limite"):
                ai.generate_course_content("Cours", "beginner")
        with patch("urllib.request.urlopen", side_effect=socket.timeout()):
            with self.assertRaisesMessage(ai.AIError, "ne répond pas"):
                ai.generate_course_content("Cours", "beginner")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaisesMessage(ai.AIError, "contacter"):
                ai.generate_course_content("Cours", "beginner")

    @override_settings(ANTHROPIC_API_KEY="test-key", AI_TIMEOUT=2)
    def test_generate_rejects_incomplete_content(self):
        payload = api_payload(objectives=["Un seul objectif"])
        with patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            with self.assertRaisesMessage(ai.AIError, "incomplète"):
                ai.generate_course_content("Cours", "beginner")
        payload = {"content": [{"type": "text", "text": "pas du json"}]}
        with patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            with self.assertRaisesMessage(ai.AIError, "invalide"):
                ai.generate_course_content("Cours", "beginner")

    def test_ai_report_aggregates_logs(self):
        self.ai_collection.insert_many([
            {"duration_ms": 100, "success": True},
            {"duration_ms": 200, "success": True},
            {"duration_ms": 900, "success": False},
        ])
        stats = ai.get_ai_stats()
        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["average_ms"], 400.0)
        self.assertEqual(stats["p95_ms"], 900)
        self.assertEqual(stats["failure_rate"], 33.3)
        output = StringIO()
        call_command("ai_report", stdout=output)
        self.assertIn("Appels: 3", output.getvalue())

    def test_record_ai_log_ignores_storage_errors(self):
        with patch.object(ai.ai_logs_collection, "insert_one", side_effect=ai.PyMongoError):
            ai.record_ai_log(duration_ms=10, success=False, error="erreur")
        self.assertEqual(self.ai_collection.count_documents({}), 0)

    def test_ai_report_handles_storage_errors(self):
        with patch(
            "apps.courses.management.commands.ai_report.get_ai_stats",
            side_effect=ai.PyMongoError,
        ):
            with self.assertRaisesMessage(CommandError, "Could not read"):
                call_command("ai_report", stdout=StringIO())
