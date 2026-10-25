"""Tests for the back-office AI evaluation report page."""
from __future__ import annotations

import json
import re
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from apps.courses.ai_eval.report import (
    AIReportInvalidError,
    AIReportNotFoundError,
    build_report_view,
    load_report,
)

from .base import MongoTestCase, user_document

MOCK_BANNER = "Évaluation simulée, non représentative"


def report_payload(**overrides):
    payload = {
        "mode": "real",
        "generated_at": "2026-10-25T12:34:56+00:00",
        "model": "openai/gpt-oss-20b",
        "coverage": {"answered": 3, "total": 4, "percent": 75.0},
        "errors": {
            "api_total": 1,
            "rate_limit": 1,
            "timeout": 0,
            "other": 0,
            "invalid_json_enum": 1,
        },
        "latency_ms": {"mean": 120.0, "p95": 200.0},
        "accuracy": 0.6667,
        "macro_f1": 0.6,
        "per_class": {
            "web": {"precision": 1.0, "recall": 0.5, "f1": 0.6667, "support": 2},
            "data": {"precision": 0.5, "recall": 1.0, "f1": 0.6667, "support": 1},
        },
        "confusion_matrix": {
            "labels": ["web", "data"],
            "rows": [[1, 1], [0, 1]],
        },
        "levels": {
            "accuracy": 1.0,
            "macro_f1": 1.0,
            "per_class": {
                "beginner": {
                    "precision": 1.0,
                    "recall": 1.0,
                    "f1": 1.0,
                    "support": 3,
                }
            },
            "confusion_matrix": {"labels": ["beginner"], "rows": [[3]]},
        },
        "analysis": "Les métriques portent sur les cas répondus.\n\nPoints forts : sortie JSON validée.",
    }
    payload.update(overrides)
    return payload


@contextmanager
def report_file(payload=None, *, raw: str | None = None):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "report.json"
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
        elif payload is not None:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with patch("apps.courses.ai_eval.report.REPORT_JSON_PATH", path):
            yield path


class TestReportLoader(SimpleTestCase):
    def test_loads_a_valid_report_from_an_explicit_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report_payload()), encoding="utf-8")
            self.assertEqual(load_report(path)["mode"], "real")

    def test_missing_file_raises_not_found(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "absent.json"
            with self.assertRaises(AIReportNotFoundError):
                load_report(path)

    def test_malformed_json_raises_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(AIReportInvalidError):
                load_report(path)

    def test_invalid_schemas_are_rejected(self):
        cases = {
            "not an object": [],
            "bad mode": report_payload(mode="autre"),
            "missing date": {k: v for k, v in report_payload().items() if k != "generated_at"},
            "missing model": {k: v for k, v in report_payload().items() if k != "model"},
            "bad coverage": report_payload(coverage={"answered": 5, "total": 4, "percent": 125.0}),
            "bad errors": report_payload(errors={"api_total": 0}),
            "bad latency": report_payload(latency_ms={"mean": "vite", "p95": 1}),
            "bad analysis": report_payload(analysis=42),
        }
        cases["bad metrics"] = report_payload(accuracy="haut")
        empty_per_class = report_payload()
        empty_per_class["levels"] = dict(empty_per_class["levels"], per_class={})
        cases["empty per class"] = empty_per_class
        cases["bad matrix"] = report_payload(
            confusion_matrix={"labels": ["web", "data"], "rows": [[1], [0, 1]]}
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            for label, payload in cases.items():
                with self.subTest(label=label):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(AIReportInvalidError):
                        load_report(path)

    def test_unparseable_generation_date_falls_back_to_raw_value(self):
        view = build_report_view(report_payload(generated_at="date inconnue"))
        self.assertEqual(view["generated_at_display"], "date inconnue")

    def test_absent_metrics_are_treated_as_unavailable(self):
        payload = report_payload()
        for key in ("accuracy", "macro_f1", "per_class", "confusion_matrix", "levels"):
            payload.pop(key)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            view = build_report_view(load_report(path))
        self.assertIsNone(view["categories"])
        self.assertIsNone(view["levels"])
        self.assertIsNone(view["accuracy"])


@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class TestAiReportPage(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.instructor = user_document(role="instructor", email="instr-report@example.com")
        self.student = user_document(role="student", email="student-report@example.com")
        self.admin = user_document(role="admin", email="admin-report@example.com")

    def _client(self, user):
        client = Client()
        session = client.session
        session["user_id"] = str(user["_id"])
        session.save()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("apps.users.decorators.get_active_user", return_value=user))
        stack.enter_context(patch("apps.courses.views.get_active_user", return_value=user))
        return client

    def test_route_is_available_in_both_namespaces(self):
        self.assertEqual(
            reverse("courses:course_manage_ai_report"),
            "/backoffice/cours/manage/ai/report/",
        )
        self.assertEqual(
            reverse("users:course_manage_ai_report"),
            "/users/backoffice/cours/manage/ai/report/",
        )

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("users:course_manage_ai_report"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("users:login"))

    def test_student_gets_403(self):
        response = self._client(self.student).get(reverse("users:course_manage_ai_report"))
        self.assertEqual(response.status_code, 403)

    def test_instructor_can_read_the_report(self):
        with report_file(report_payload()):
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        self.assertEqual(response.status_code, 200)

    def test_admin_can_read_the_report(self):
        with report_file(report_payload()):
            response = self._client(self.admin).get(
                reverse("users:course_manage_ai_report")
            )
        self.assertEqual(response.status_code, 200)

    def test_post_is_not_allowed(self):
        with report_file(report_payload()):
            response = self._client(self.instructor).post(
                reverse("users:course_manage_ai_report")
            )
        self.assertEqual(response.status_code, 405)

    def test_missing_report_shows_friendly_empty_state(self):
        with report_file():
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Aucun rapport IA disponible", html)
        self.assertIn("python manage.py ai_report --eval", html)
        self.assertNotIn(MOCK_BANNER, html)

    def test_malformed_report_shows_invalid_state(self):
        with report_file(raw="{ pas du json"):
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Rapport IA illisible", html)
        self.assertIn("python manage.py ai_report --eval", html)

    def test_schema_invalid_report_shows_invalid_state(self):
        with report_file(report_payload(mode="inconnu")):
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        self.assertIn("Rapport IA illisible", response.content.decode())

    def test_mock_mode_displays_the_banner(self):
        with report_file(report_payload(mode="mock")):
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        html = response.content.decode()
        self.assertIn(MOCK_BANNER, html)
        self.assertIn("non représentative", html)

    def test_real_mode_hides_the_mock_banner(self):
        with report_file(report_payload(mode="real")):
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        self.assertNotIn(MOCK_BANNER, response.content.decode())

    def test_values_are_rendered(self):
        with report_file(report_payload()):
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        html = response.content.decode()
        self.assertIn("openai/gpt-oss-20b", html)
        self.assertIn("3/4", html)
        self.assertIn("75,0 %", html)
        self.assertIn("66,7 %", html)
        self.assertIn("0,600", html)  # macro-F1
        self.assertIn("120 ms", html)
        self.assertIn("200 ms", html)
        self.assertIn("0,500", html)  # per-class precision
        self.assertIn("<td>2</td>", html)  # support
        self.assertIn("25/10/2026 à 12:34", html)

    def test_analysis_is_escaped(self):
        payload = report_payload(
            analysis='<script>alert("xss")</script> <img src=x onerror=alert(1)>'
        )
        with report_file(payload):
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        html = response.content.decode()
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x onerror", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)

    def test_page_has_exactly_one_h1(self):
        with report_file(report_payload()):
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        html = response.content.decode()
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn("Rapport d'évaluation IA", html)

    def test_all_tables_have_captions(self):
        with report_file(report_payload()):
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        html = response.content.decode()
        table_count = html.count("<table")
        captioned = re.findall(r"<table[^>]*>\s*<caption>", html)
        self.assertGreaterEqual(table_count, 4)
        self.assertEqual(len(captioned), table_count)
        self.assertIn("Précision, rappel et F1 par catégorie", html)
        self.assertIn("Matrice de confusion — catégories", html)
        self.assertIn("Précision, rappel et F1 par niveau", html)
        self.assertIn("Matrice de confusion — niveaux", html)
        self.assertIn('scope="col"', html)
        self.assertIn('scope="row"', html)

    def test_confusion_matrix_diagonal_is_bold(self):
        with report_file(report_payload()):
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        html = response.content.decode()
        self.assertRegex(
            html,
            r'<td class="is-diagonal">\s*<strong>1</strong>',
        )

    def test_no_answered_case_shows_unavailable_metrics(self):
        with report_file(
            report_payload(
                accuracy=None,
                macro_f1=None,
                per_class=None,
                confusion_matrix=None,
                levels=None,
            )
        ):
            response = self._client(self.instructor).get(
                reverse("users:course_manage_ai_report")
            )
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Métriques indisponibles", html)

    def test_manage_list_links_to_the_report(self):
        response = self._client(self.instructor).get(
            reverse("users:course_manage_list")
        )
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Rapport IA", html)
        self.assertIn(reverse("users:course_manage_ai_report"), html)
