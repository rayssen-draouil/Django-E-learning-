import json
from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from . import services
from .forms import QuizAIGenerationForm, QuizForm, QuizImportForm, QuizSubmissionForm
from .ai.quiz_generator import InvalidGeneratedQuiz, generate_quiz
from .ai.gemini_client import GeminiConfigurationError, GeminiGenerationError, generate_json


def question_data(**overrides):
    result = {"type": "single_choice", "prompt": "2 + 2 ?", "points": 2,
              "options": [{"text": "3"}, {"text": "4"}], "correct_option_ids": [1]}
    result.update(overrides)
    return result


def form_data(**overrides):
    result = {"title": "Arithmétique", "description": "Test", "status": "draft",
              "duration_seconds": "60", "passing_score": "50",
              "questions_json": json.dumps([question_data()])}
    result.update(overrides)
    return result


class QuizFormTests(SimpleTestCase):
    def test_valid_question_generates_object_ids_and_normalizes_correct_choice(self):
        form = QuizForm(form_data())
        self.assertTrue(form.is_valid(), form.errors)
        question = form.to_document()["questions"][0]
        self.assertIsInstance(question["_id"], ObjectId)
        self.assertEqual(question["correct_option_ids"], [question["options"][1]["_id"]])

    def test_edit_form_prefills_quiz_attributes_and_preserves_question_option_ids(self):
        question_id, option_id = ObjectId(), ObjectId()
        quiz = {"title": "Quiz existant", "description": "Description conservée", "status": "draft",
                "duration_seconds": 90, "passing_score": 70,
                "questions": [{"_id": question_id, "type": "single_choice", "prompt": "Question conservée",
                               "points": 3, "options": [{"_id": option_id, "text": "Réponse A"},
                                                             {"_id": ObjectId(), "text": "Réponse B"}],
                               "correct_option_ids": [option_id]}]}
        form = QuizForm(None, quiz=quiz)
        self.assertFalse(form.is_bound)
        self.assertEqual(form["title"].value(), "Quiz existant")
        self.assertEqual(form["duration_seconds"].value(), 90)
        self.assertEqual(form["passing_score"].value(), 70)
        form = QuizForm(form.initial, quiz=quiz)
        self.assertTrue(form.is_valid(), form.errors)
        question = form.to_document()["questions"][0]
        self.assertEqual(question["_id"], question_id)
        self.assertEqual(question["options"][0]["_id"], option_id)
        self.assertEqual(question["correct_option_ids"], [option_id])

    def test_rejects_invalid_type(self):
        self.assertFalse(QuizForm(form_data(questions_json=json.dumps([question_data(type="essay")]))).is_valid())

    def test_rejects_missing_or_invalid_options_and_correct_option_reference(self):
        for changed in (
            question_data(options=[{"text": "only one"}]),
            question_data(options=[{"text": "A"}, {"text": "  "}]),
            question_data(correct_option_ids=[4]),
            question_data(correct_option_ids=[]),
        ):
            with self.subTest(changed=changed):
                self.assertFalse(QuizForm(form_data(questions_json=json.dumps([changed]))).is_valid())

    def test_rejects_questions_and_options_above_supported_limits(self):
        too_many_questions = [question_data() for _ in range(101)]
        self.assertFalse(QuizForm(form_data(questions_json=json.dumps(too_many_questions))).is_valid())
        too_many_options = question_data(options=[{"text": f"Option {index}"} for index in range(21)], correct_option_ids=[0])
        self.assertFalse(QuizForm(form_data(questions_json=json.dumps([too_many_options]))).is_valid())

    def test_multiple_choice_requires_at_least_one_correct_choice(self):
        question = question_data(type="multiple_choice", correct_option_ids=[0, 1])
        self.assertTrue(QuizForm(form_data(questions_json=json.dumps([question]))).is_valid())

    def test_true_false_requires_vrai_faux_options(self):
        question = question_data(type="true_false", options=[{"text": "Vrai"}, {"text": "Faux"}])
        self.assertTrue(QuizForm(form_data(questions_json=json.dumps([question]))).is_valid())
        question["options"] = [{"text": "Oui"}, {"text": "Non"}]
        self.assertFalse(QuizForm(form_data(questions_json=json.dumps([question]))).is_valid())


class QuizImportFormTests(SimpleTestCase):
    def test_accepts_quiz_json_file_and_normalizes_extended_object_ids(self):
        option_id = ObjectId()
        data = {"title": "Importé", "description": "JSON", "status": "draft",
                "questions": [{"type": "single_choice", "prompt": "Question", "points": 1,
                "options": [{"_id": {"$oid": str(option_id)}, "text": "Oui"}, {"text": "Non"}],
                "correct_option_ids": [{"$oid": str(option_id)}]}]}
        file = SimpleUploadedFile("quiz.json", json.dumps(data).encode(), content_type="application/json")
        form = QuizImportForm(files={"quiz_file": file})
        self.assertTrue(form.is_valid(), form.errors)
        imported = form.cleaned_data["quiz_file"]
        self.assertEqual(imported["questions"][0]["options"][0]["_id"], str(option_id))

    def test_rejects_invalid_json_and_oversized_files(self):
        invalid = QuizImportForm(files={"quiz_file": SimpleUploadedFile("quiz.json", b"{invalid")})
        self.assertFalse(invalid.is_valid())
        oversized = QuizImportForm(files={"quiz_file": SimpleUploadedFile("quiz.json", b" " * 1_000_001)})
        self.assertFalse(oversized.is_valid())

    def test_rejects_invalid_json_and_empty_question_list(self):
        for value in ("{bad", "[]", "{}"):
            with self.subTest(value=value):
                self.assertFalse(QuizForm(form_data(questions_json=value)).is_valid())

    def test_validates_explicit_option_object_ids_and_correct_references(self):
        option_id = ObjectId()
        question = question_data(options=[{"_id": str(option_id), "text": "A"}, {"text": "B"}],
                                 correct_option_ids=[str(option_id)])
        form = QuizForm(form_data(questions_json=json.dumps([question])))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.to_document()["questions"][0]["correct_option_ids"], [option_id])
        question["options"][0]["_id"] = str(option_id)
        question["correct_option_ids"] = [str(ObjectId())]
        self.assertFalse(QuizForm(form_data(questions_json=json.dumps([question]))).is_valid())
        question["options"][0]["_id"] = "invalid-id"
        self.assertFalse(QuizForm(form_data(questions_json=json.dumps([question]))).is_valid())


class QuizSubmissionFormTests(SimpleTestCase):
    def test_unanswered_questions_are_empty_answers_for_timed_auto_submission(self):
        question_id = ObjectId()
        quiz = {"questions": [{"_id": question_id, "type": "single_choice", "prompt": "Question",
                               "options": [{"_id": ObjectId(), "text": "Oui"},
                                           {"_id": ObjectId(), "text": "Non"}]}]}
        form = QuizSubmissionForm({}, quiz=quiz)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.selected_answers(), {question_id: []})


class QuizServiceTests(SimpleTestCase):
    def setUp(self):
        self.author = {"_id": ObjectId(), "role": "instructor"}
        self.document = {"_id": ObjectId(), "created_by": self.author["_id"], "title": "Quiz"}

    def test_create_read_update_and_delete(self):
        form = QuizForm(form_data())
        self.assertTrue(form.is_valid())
        with patch("apps.quizzes.services.repositories.insert_quiz", return_value=self.document["_id"]) as insert:
            self.assertEqual(services.create_quiz(data=form.to_document(), user=self.author), self.document["_id"])
        inserted = insert.call_args.args[0]
        self.assertEqual(inserted["created_by"], self.author["_id"])
        self.assertIsInstance(inserted["created_at"], datetime)
        with patch("apps.quizzes.services.repositories.get_quiz", return_value=self.document):
            self.assertEqual(services.get_managed_quiz(str(self.document["_id"]), self.author), self.document)
        with patch("apps.quizzes.services.repositories.get_quiz", return_value=self.document), \
             patch("apps.quizzes.services.repositories.update_quiz", return_value=True) as update:
            services.update_managed_quiz(str(self.document["_id"]), data=form.to_document(), user=self.author)
            self.assertEqual(update.call_args.args[0], self.document["_id"])
        with patch("apps.quizzes.services.repositories.get_quiz", return_value=self.document), \
             patch("apps.quizzes.services.repositories.delete_quiz", return_value=True) as delete:
            services.delete_managed_quiz(str(self.document["_id"]), user=self.author)
            delete.assert_called_once_with(self.document["_id"])

    def test_rejects_malformed_id(self):
        with self.assertRaises(services.QuizNotFoundError):
            services.get_managed_quiz("not-an-object-id", self.author)

    def test_author_cannot_read_update_or_delete_another_authors_quiz(self):
        other = {"_id": ObjectId(), "role": "instructor"}
        with patch("apps.quizzes.services.repositories.get_quiz", return_value=self.document):
            with self.assertRaises(services.QuizPermissionError):
                services.get_managed_quiz(str(self.document["_id"]), other)
            with self.assertRaises(services.QuizPermissionError):
                services.update_managed_quiz(str(self.document["_id"]), data={}, user=other)
            with self.assertRaises(services.QuizPermissionError):
                services.delete_managed_quiz(str(self.document["_id"]), user=other)

    def test_admin_can_manage_another_authors_quiz(self):
        with patch("apps.quizzes.services.repositories.get_quiz", return_value=self.document):
            self.assertEqual(services.get_managed_quiz(str(self.document["_id"]), {"_id": ObjectId(), "role": "admin"}), self.document)

    def test_publish_sets_status_only_for_owned_quiz(self):
        self.document["questions"] = [{"prompt": "Question"}]
        with patch("apps.quizzes.services.repositories.get_quiz", return_value=self.document), \
             patch("apps.quizzes.services.repositories.update_quiz", return_value=True) as update:
            services.publish_managed_quiz(str(self.document["_id"]), user=self.author)
        self.assertEqual(update.call_args.args[1]["status"], "published")

    def test_publish_rejects_quiz_without_questions(self):
        with patch("apps.quizzes.services.repositories.get_quiz", return_value=self.document):
            with self.assertRaises(services.QuizSubmissionError):
                services.publish_managed_quiz(str(self.document["_id"]), user=self.author)

    def test_submission_is_scored_server_side_and_saved_as_attempt(self):
        qid, correct_id, wrong_id = ObjectId(), ObjectId(), ObjectId()
        quiz = {"_id": self.document["_id"], "title": "Quiz", "status": "published",
                "passing_score": 50, "questions": [{"_id": qid, "position": 1,
                "type": "single_choice", "prompt": "Question", "points": 4,
                "options": [{"_id": correct_id, "text": "Correct"}, {"_id": wrong_id, "text": "Wrong"}],
                "correct_option_ids": [correct_id]}]}
        with patch("apps.quizzes.services.repositories.get_quiz", return_value=quiz), \
             patch("apps.quizzes.services.repositories.insert_attempt", side_effect=lambda doc: doc["_id"]) as insert:
            attempt_id = services.submit_quiz(str(quiz["_id"]), user=self.author,
                                               answers={qid: [str(correct_id)]})
        saved = insert.call_args.args[0]
        self.assertEqual(attempt_id, saved["_id"])
        self.assertEqual(saved["score_percentage"], 100)
        self.assertTrue(saved["passed"])
        self.assertTrue(saved["answers"][0]["is_correct"])

    def test_submission_rejects_an_option_from_another_question(self):
        qid, option_id = ObjectId(), ObjectId()
        quiz = {"_id": self.document["_id"], "title": "Quiz", "status": "published",
                "questions": [{"_id": qid, "prompt": "Question", "points": 1,
                "options": [{"_id": ObjectId(), "text": "Valid"}],
                "correct_option_ids": [ObjectId()]}]}
        with patch("apps.quizzes.services.repositories.get_quiz", return_value=quiz):
            with self.assertRaises(services.QuizSubmissionError):
                services.submit_quiz(str(quiz["_id"]), user=self.author,
                                     answers={qid: [str(option_id)]})

    def test_student_stats_aggregate_attempts_and_question_accuracy(self):
        qid = ObjectId()
        attempts = [
            {"_id": ObjectId(), "quiz_id": ObjectId(), "score_percentage": 100, "passed": True,
             "answers": [{"question_id": qid, "prompt": "Question", "is_correct": True}]},
            {"_id": ObjectId(), "quiz_id": ObjectId(), "score_percentage": 40, "passed": False,
             "answers": [{"question_id": qid, "prompt": "Question", "is_correct": False}]},
        ]
        with patch("apps.quizzes.services.repositories.list_student_attempts", return_value=attempts):
            stats = services.get_student_quiz_stats(self.author)
        self.assertEqual(stats["attempt_count"], 2)
        self.assertEqual(stats["average_score"], 70)
        self.assertEqual(stats["best_score"], 100)
        self.assertEqual(stats["question_stats"][0]["accuracy"], 50)


@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class QuizViewTests(SimpleTestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = {"_id": ObjectId(), "first_name": "Ada", "last_name": "Lovelace", "role": "instructor"}

    def authenticate_as(self, role=None):
        session = self.client.session
        session["user_id"] = str(self.user["_id"])
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        user = dict(self.user, role=role or self.user["role"])
        return patch("apps.users.decorators.get_active_user", return_value=user)

    def csrf_post(self, url, data):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        token = response.cookies.get("csrftoken")
        self.assertIsNotNone(token)
        return self.client.post(url, data, HTTP_X_CSRFTOKEN=token.value)

    def test_management_routes_keep_authentication_and_roles(self):
        urls = [reverse("users:quiz_manage_list"), reverse("users:quiz_manage_create")]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)
        with self.authenticate_as("student"):
            for url in urls:
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_legacy_route_names_remain(self):
        self.assertEqual(reverse("users:quiz"), "/users/quiz/")
        self.assertEqual(reverse("users:backoffice_quizzes"), "/users/backoffice/quiz/")

    def test_frontoffice_lists_published_quizzes_with_answer_links(self):
        quiz_id = ObjectId()
        published = {"_id": quiz_id, "title": "Quiz publié", "description": "Test",
                     "questions": [{"correct_option_ids": [ObjectId()]}]}
        with patch("apps.quizzes.views.list_published_quizzes", return_value=[published]):
            response = self.client.get(reverse("users:quiz"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quiz publié")
        self.assertContains(response, reverse("users:quiz_take", args=[str(quiz_id)]))

    def test_invalid_id_returns_404_and_owner_is_enforced(self):
        with self.authenticate_as():
            self.assertEqual(self.client.get(reverse("users:quiz_manage_detail", args=["invalid"])).status_code, 404)
            other_quiz = {"_id": ObjectId(), "created_by": ObjectId(), "title": "Other"}
            with patch("apps.quizzes.services.repositories.get_quiz", return_value=other_quiz):
                self.assertEqual(self.client.get(reverse("users:quiz_manage_detail", args=[str(other_quiz["_id"])] )).status_code, 403)

    def test_detail_edit_and_delete_pages_render_for_the_owner(self):
        option_a, option_b = ObjectId(), ObjectId()
        quiz = {"_id": ObjectId(), "created_by": self.user["_id"], "title": "Quiz de test",
                "description": "Description", "status": "draft", "duration_seconds": None,
                "passing_score": None, "questions": [{"_id": ObjectId(), "position": 1,
                "type": "single_choice", "prompt": "Question ?", "points": 1,
                "options": [{"_id": option_a, "text": "Oui"}, {"_id": option_b, "text": "Non"}],
                "correct_option_ids": [option_a]}]}
        with self.authenticate_as(), patch("apps.quizzes.services.repositories.get_quiz", return_value=quiz):
            for route_name in ("users:quiz_manage_detail", "users:quiz_manage_edit", "users:quiz_manage_delete"):
                with self.subTest(route=route_name):
                    response = self.client.get(reverse(route_name, args=[str(quiz["_id"])]))
                    self.assertEqual(response.status_code, 200)

    def test_create_uses_post_csrf_and_persists(self):
        with self.authenticate_as(), patch("apps.quizzes.views.create_quiz", return_value=ObjectId()) as create:
            response = self.csrf_post(reverse("users:quiz_manage_create"), form_data())
            self.assertEqual(response.status_code, 302)
            create.assert_called_once()
        client_without_csrf = Client(enforce_csrf_checks=True)
        session = client_without_csrf.session
        session["user_id"] = str(self.user["_id"])
        session.save()
        client_without_csrf.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        with patch("apps.users.decorators.get_active_user", return_value=self.user):
            response = client_without_csrf.post(reverse("users:quiz_manage_create"), form_data())
        self.assertEqual(response.status_code, 403)

    def test_json_import_creates_a_draft_and_redirects_to_preview(self):
        data = {"title": "Quiz importé", "description": "Depuis un fichier", "status": "published",
                "questions": [question_data()]}
        file = SimpleUploadedFile("quiz.json", json.dumps(data).encode(), content_type="application/json")
        url = reverse("users:quiz_manage_create")
        quiz_id = ObjectId()
        with self.authenticate_as(), patch("apps.quizzes.views.create_quiz", return_value=quiz_id) as create:
            get_response = self.client.get(url)
            token = get_response.cookies["csrftoken"].value
            response = self.client.post(url, {"quiz_file": file, "import_json": "1"},
                                        HTTP_X_CSRFTOKEN=token)
            self.assertRedirects(response, reverse("users:quiz_manage_detail", args=[str(quiz_id)]), fetch_redirect_response=False)
            self.assertEqual(create.call_args.kwargs["data"]["status"], "draft")
            create.assert_called_once()

    def test_student_can_answer_published_quiz_and_is_redirected_to_result(self):
        self.user["role"] = "student"
        quiz_id, question_id = ObjectId(), ObjectId()
        correct_id, wrong_id, attempt_id = ObjectId(), ObjectId(), ObjectId()
        quiz = {"_id": quiz_id, "title": "Quiz public", "description": "Description",
                "status": "published", "passing_score": 50,
                "questions": [{"_id": question_id, "position": 1, "type": "single_choice",
                "prompt": "Question ?", "points": 1,
                "options": [{"_id": correct_id, "text": "Bonne"}, {"_id": wrong_id, "text": "Mauvaise"}],
                "correct_option_ids": [correct_id]}]}
        url = reverse("users:quiz_take", args=[str(quiz_id)])
        with self.authenticate_as("student"), \
             patch("apps.quizzes.services.repositories.get_quiz", return_value=quiz), \
             patch("apps.quizzes.services.repositories.insert_attempt", return_value=attempt_id):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            field_name = f"question_{question_id}"
            response = self.csrf_post(url, {field_name: str(correct_id)})
            self.assertRedirects(response, reverse("users:quiz_result", args=[str(attempt_id)]), fetch_redirect_response=False)

    def test_timed_quiz_renders_a_countdown_from_its_configured_duration(self):
        quiz_id = ObjectId()
        quiz = {"_id": quiz_id, "title": "Quiz chronométré", "description": "Durée configurée",
                "status": "published", "duration_seconds": 125,
                "questions": [{"_id": ObjectId(), "position": 1, "type": "single_choice",
                "prompt": "Question ?", "points": 2,
                "options": [{"_id": ObjectId(), "text": "Oui"}, {"_id": ObjectId(), "text": "Non"}],
                "correct_option_ids": []}]}
        with self.authenticate_as("admin"), patch("apps.quizzes.views.get_published_quiz", return_value=quiz):
            response = self.client.get(reverse("users:quiz_take", args=[str(quiz_id)]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-duration="125"')
        self.assertContains(response, "quiz-countdown")
        self.assertContains(response, "QUESTION 01")

    def test_admin_can_open_frontoffice_quiz_to_answer(self):
        quiz_id = ObjectId()
        quiz = {"_id": quiz_id, "title": "Quiz côté apprenant", "description": "Test",
                "status": "published", "questions": [{"_id": ObjectId(), "position": 1, "type": "single_choice",
                "prompt": "Question ?", "points": 1,
                "options": [{"_id": ObjectId(), "text": "Oui"}, {"_id": ObjectId(), "text": "Non"}],
                "correct_option_ids": []}]}
        with self.authenticate_as("admin"), patch("apps.quizzes.views.get_published_quiz", return_value=quiz):
            response = self.client.get(reverse("users:quiz_take", args=[str(quiz_id)]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quiz côté apprenant")

    def test_student_can_view_only_their_own_attempt_result(self):
        self.user["role"] = "student"
        attempt_id, option_id = ObjectId(), ObjectId()
        attempt = {"_id": attempt_id, "user_id": self.user["_id"], "quiz_title": "Quiz",
                   "score": 1, "max_score": 1, "score_percentage": 100, "passed": True,
                   "passing_score": 50, "submitted_at": datetime.now(timezone.utc),
                   "answers": [{"prompt": "Question", "is_correct": True,
                   "points_awarded": 1, "points_possible": 1,
                   "selected_option_ids": [option_id], "correct_option_ids": [option_id],
                   "options": [{"_id": option_id, "text": "Oui"}]}]}
        url = reverse("users:quiz_result", args=[str(attempt_id)])
        with self.authenticate_as("student"), patch(
            "apps.quizzes.services.repositories.get_student_attempt", return_value=attempt
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Quiz")
            self.assertContains(response, "100 %")
        with self.authenticate_as("student"), patch(
            "apps.quizzes.services.repositories.get_student_attempt", return_value=None
        ):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_delete_confirmation_is_get_and_delete_requires_post(self):
        quiz = {"_id": ObjectId(), "created_by": self.user["_id"], "title": "Quiz", "questions": []}
        with self.authenticate_as(), patch("apps.quizzes.services.repositories.get_quiz", return_value=quiz):
            response = self.client.get(reverse("users:quiz_manage_delete", args=[str(quiz["_id"])]))
            self.assertEqual(response.status_code, 200)
            with patch("apps.quizzes.views.delete_managed_quiz") as delete:
                post = self.csrf_post(reverse("users:quiz_manage_delete", args=[str(quiz["_id"])]), {})
                self.assertEqual(post.status_code, 302)
                delete.assert_called_once()

    def _csrf_json_post(self, url, payload):
        response = self.client.get(reverse("users:quiz_manage_list"))
        self.assertEqual(response.status_code, 200)
        token = response.cookies["csrftoken"].value
        return self.client.post(url, json.dumps(payload), content_type="application/json",
                                HTTP_X_CSRFTOKEN=token)

    def _ai_payload(self):
        return {"topic": "Fractions", "description": "Quiz de mathématiques",
                "objectives": "Comparer des fractions", "question_count": "5",
                "difficulty": "beginner", "question_type": "single_choice", "language": "fr"}

    def _generated_quiz(self):
        return {"title": "Fractions", "description": "Évaluation des fractions", "questions": [
            {"type": "single_choice", "prompt": f"Question {number} ?", "points": 1,
             "options": [{"text": "A"}, {"text": "B"}], "correct_option_ids": [0]}
            for number in range(1, 6)
        ]}

    def test_ai_generation_is_validated_and_does_not_persist_before_confirmation(self):
        with self.authenticate_as(), \
             patch("apps.quizzes.views.generate_quiz", return_value=self._generated_quiz()), \
             patch("apps.quizzes.views.create_quiz") as create:
            response = self._csrf_json_post(reverse("users:quiz_ai_generate"), self._ai_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["quiz"]["questions"]), 5)
        create.assert_not_called()

    def test_ai_draft_is_created_only_after_valid_confirmation_as_current_user(self):
        quiz_id = ObjectId()
        generated = self._generated_quiz()
        payload = {"title": generated["title"], "description": generated["description"],
                   "questions": generated["questions"]}
        with self.authenticate_as(), patch("apps.quizzes.views.create_quiz", return_value=quiz_id) as create:
            response = self._csrf_json_post(reverse("users:quiz_ai_create_draft"), payload)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["redirect_url"], reverse("users:quiz_manage_detail", args=[str(quiz_id)]))
        self.assertEqual(create.call_args.kwargs["user"]["_id"], self.user["_id"])
        self.assertEqual(create.call_args.kwargs["data"]["status"], "draft")

    def test_ai_endpoints_reject_anonymous_students_and_missing_csrf(self):
        url = reverse("users:quiz_ai_generate")
        self.assertEqual(self.client.post(url, "{}", content_type="application/json").status_code, 403)
        with self.authenticate_as("student"):
            response = self.client.post(url, "{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)

        csrf_client = Client(enforce_csrf_checks=True)
        session = csrf_client.session
        session["user_id"] = str(self.user["_id"])
        session.save()
        csrf_client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        with patch("apps.users.decorators.get_active_user", return_value=self.user):
            response = csrf_client.post(url, json.dumps(self._ai_payload()), content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_ai_validation_errors_and_generation_failures_are_safe(self):
        with self.authenticate_as():
            invalid = self._csrf_json_post(reverse("users:quiz_ai_generate"), {**self._ai_payload(), "question_count": "2"})
            self.assertEqual(invalid.status_code, 400)
            with patch("apps.quizzes.views.generate_quiz", side_effect=InvalidGeneratedQuiz):
                failed = self._csrf_json_post(reverse("users:quiz_ai_generate"), self._ai_payload())
            with patch("apps.quizzes.views.generate_quiz", side_effect=GeminiConfigurationError):
                unavailable = self._csrf_json_post(reverse("users:quiz_ai_generate"), self._ai_payload())
            with patch("apps.quizzes.views.generate_quiz", side_effect=GeminiGenerationError):
                provider_failed = self._csrf_json_post(reverse("users:quiz_ai_generate"), self._ai_payload())
            with patch("apps.quizzes.views._consume_ai_generation_slot", return_value=False):
                limited = self._csrf_json_post(reverse("users:quiz_ai_generate"), self._ai_payload())
        self.assertEqual(failed.status_code, 502)
        self.assertIn("validation", failed.json()["error"])
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(provider_failed.status_code, 502)
        self.assertIn("Check the API key", provider_failed.json()["error"])
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited)


class QuizAIGenerationTests(SimpleTestCase):
    def test_form_requires_topic_description_and_objectives(self):
        form = QuizAIGenerationForm({"topic": " ", "description": "", "objectives": "",
                                     "question_count": "5", "difficulty": "beginner",
                                     "question_type": "mixed", "language": "fr"})
        self.assertFalse(form.is_valid())
        self.assertTrue({"topic", "description", "objectives"}.issubset(form.errors))

    def test_generator_normalizes_structured_response_and_rejects_invalid_answers(self):
        params = {"topic": "Fractions", "description": "Quiz", "objectives": "Comparer",
                  "question_count": 5, "difficulty": "beginner", "question_type": "single_choice", "language": "fr"}
        valid = {"title": "Fractions", "description": "Test", "questions": [
            {"type": "single_choice", "prompt": f"Question {i}?", "points": 1,
             "options": ["A", "B"], "correct_option_indices": [1]} for i in range(5)
        ]}
        result = generate_quiz(params, json_generator=lambda prompt, schema: json.dumps(valid))
        self.assertEqual(result["questions"][0]["correct_option_ids"], [1])
        valid["questions"][0]["correct_option_indices"] = [9]
        with self.assertRaises(InvalidGeneratedQuiz):
            generate_quiz(params, json_generator=lambda prompt, schema: json.dumps(valid))

    @override_settings(GROQ_API_KEY="", GEMINI_API_KEY="")
    def test_generate_json_raises_configuration_error_when_no_keys(self):
        with self.assertRaises(GeminiConfigurationError):
            generate_json("prompt", {})

    @override_settings(GROQ_API_KEY="test-groq-key", GROQ_BASE_URL="https://api.groq.com/openai/v1")
    def test_generate_json_calls_groq_successfully(self):
        sample_json = '{"title": "Test", "description": "Desc", "questions": []}'
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": f"```json\n{sample_json}\n```"}}]
            }).encode("utf-8")
            result = generate_json("create a quiz", {})
        self.assertEqual(result, sample_json)

    @override_settings(GROQ_API_KEY="test-groq-key")
    def test_generate_json_raises_generation_error_on_network_failure(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            with self.assertRaises(GeminiGenerationError):
                generate_json("prompt", {})

