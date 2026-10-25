"""Tests for the support AI assistant (classification, FAQ, summary, replies).

The Groq API is simulated by intercepting ``ai._chat_content``; the local
heuristic fallback is tested when AI fails or returns invalid JSON.
"""
from unittest.mock import patch

from django.test import Client, SimpleTestCase
from bson import ObjectId
from django.urls import reverse

from .. import ai

FAKE_JSON_CALL = """Here is my analysis:
{"category": "quizzes", "priority": "high",
 "explanation": "Quiz score display issue.",
 "faq_ids": [2]}
"""


def fake_models():
    """Stable list of FAQ for testing matches."""
    return [
        {"_id": None, "question": "Sign in to account", "answer": "Reset your password.", "category": "account"},
        {"_id": None, "question": "Course video blocked", "answer": "Reload the page.", "category": "courses"},
        {"_id": None, "question": "Quiz and grading", "answer": "Check your answers.", "category": "quizzes"},
    ]


class TestAIClassification(SimpleTestCase):
    def test_classification_valid_json_from_model(self):
        with patch.object(ai, "_chat_content", return_value=FAKE_JSON_CALL):
            result = ai.ai_classify("Quiz score missing", "My score doesn't appear after the quiz.")
        self.assertIn(result["category"], ai.VALID_CATEGORIES)
        self.assertEqual(result["source"], ai.SOURCE_AI)
        self.assertEqual(result["priority"], "high")

    def test_invalid_response_falls_back_to_heuristic(self):
        with patch.object(ai, "_chat_content", return_value="sorry I can't"):
            result = ai.ai_classify("", "")
        self.assertEqual(result["source"], ai.SOURCE_LOCAL)
        self.assertIn(result["category"], ai.VALID_CATEGORIES)

    def test_api_error_forces_fallback_without_crash(self):
        with patch.object(ai, "_chat_content", side_effect=ai.AIServiceError("network")):
            classification = ai.ai_classify("Account inaccessible", "I can no longer enter.")
            suggestions = ai.ai_suggestions("Account inaccessible", "I can no longer enter.")
        self.assertEqual(classification["source"], ai.SOURCE_LOCAL)
        self.assertEqual(suggestions["source"], ai.SOURCE_LOCAL)
        self.assertTrue(suggestions["solutions"])

    def test_urgent_theme_detected_by_lexicon(self):
        self.assertEqual(ai._priority_local("nothing works at all URGENT world"), "urgent")
        self.assertEqual(ai._priority_local("a small suggestion question"), "low")
        self.assertEqual(ai._priority_local("neutral description of the issue"), "normal")


class TestSuggestionsAndSummary(SimpleTestCase):
    def setUp(self):
        self.entries = fake_models()

    def test_suggestions_class_faq(self):
        with patch("apps.support.services.faq_entries", return_value=self.entries), \
             patch("apps.support.services.search_faq", return_value=self.entries), \
             patch.object(ai, "_chat_content", return_value=FAKE_JSON_CALL):
            result = ai.ai_suggestions("My score doesn't display", "I finished my quiz")
        self.assertEqual(result["source"], ai.SOURCE_AI)
        self.assertEqual(result["solutions"][0]["question"], "Quiz and grading")

    def test_summary_with_valid_json(self):
        with patch.object(ai, "_chat_content", return_value=(
                '{"summary": "The student cannot see their score.", "problem": "score missing",'
                ' "actions_done": ["1 message(s) exchanged"]}')):
            result = ai.ai_summary({"title": "t", "description": "d"}, [], [])
        self.assertEqual(result["summary"], "The student cannot see their score.")
        self.assertEqual(result["actions_done"], ["1 message(s) exchanged"])

    def test_summary_falls_back_to_raw_text(self):
        with patch.object(ai, "_chat_content", side_effect=ai.AIServiceError("network")):
            ticket = {"title": "T", "description": "D"}
            result = ai.ai_summary(ticket, [{"sender_role": "admin", "body": "ok"}], [])
        self.assertEqual(result["source"], ai.SOURCE_LOCAL)
        self.assertIn("message", result["actions_done"][0])

    def test_ai_reply_draft(self):
        with patch.object(ai, "_chat_content", return_value=
                '{"reply": "Hello, we are checking your quiz attempt."}'):
            result = ai.ai_draft_reply(
                {"title": "score", "description": "missing"},
                [{"sender_role": "student", "body": "help"}])
        self.assertEqual(result["reply"], "Hello, we are checking your quiz attempt.")
        self.assertEqual(result["source"], ai.SOURCE_AI)

    def test_draft_falls_back_to_lexicon(self):
        with patch.object(ai, "_chat_content", side_effect=ai.AIServiceError("network")):
            result = ai.ai_draft_reply(
                {"title": "score", "description": "missing"}, [])
        self.assertTrue(result["reply"])


class TestAIViews(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        self.admin = {"_id": ObjectId(), "email": "a@example.com", "role": "admin",
                      "first_name": "A", "last_name": "Dmin", "password": "x", "is_active": True}
        self.student = {"_id": ObjectId(), "email": "s@example.com", "role": "student",
                         "first_name": "S", "last_name": "Tect", "password": "x", "is_active": True}

    def _auth(self, user):
        session = self.client.session
        session["user_id"] = str(user["_id"])
        session.save()
        self.client.cookies["sessionid"] = session.session_key
        return patch("apps.users.decorators.get_active_user", return_value=user)

    def test_suggestion_form_open_to_any_authenticated_user(self):
        with self._auth(self.student), \
             patch("apps.support.ai.ai_suggestions", return_value={"source": "groq", "solutions": []}):
            response = self.client.post(reverse("support:ai_suggestion"),
                                       {"title": "score", "description": "missing"},
                                       content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_suggestion_without_content_responds_with_message(self):
        with self._auth(self.student):
            response = self.client.post(reverse("support:ai_suggestion"),
                                       "{}", content_type="application/json")
        self.assertFalse(response.json()["ok"])

    def test_summary_without_csrf_rejected_403(self):
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session["user_id"] = str(self.admin["_id"])
        session.save()
        client.cookies["sessionid"] = session.session_key
        with patch("apps.users.decorators.get_active_user", return_value=self.admin):
            response = client.post(reverse("support:ai_summary", args=[str(self.admin["_id"])]),
                                  "{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_run_admin_summary(self):
        with self._auth(self.student):
            response = self.client.post(reverse("support:ai_summary", args=[str(self.student["_id"])]),
                                       "{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)
