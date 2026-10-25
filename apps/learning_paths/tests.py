from datetime import datetime, timezone
import json
from unittest.mock import patch

from bson import ObjectId
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from reportlab.lib.styles import getSampleStyleSheet

from apps.learning_paths.forms import LearningPathForm
from apps.learning_paths.ai import (
    LearningPathAIError,
    generate_learning_path_proposal,
    generate_learning_path_statistics_insights,
)
from apps.learning_paths.proposal_forms import LearningPathProposalForm
from apps.learning_paths.pdf import (
    _formatted_paragraph,
    _paragraph,
    build_learning_path_pdf,
)
from apps.learning_paths import views
from apps.learning_paths.services import (
    LearningPathEnrollmentRequiredError,
    LearningPathNotFoundError,
    LearningPathPermissionError,
    enroll_in_learning_path,
    get_learning_path_statistics,
    create_learning_path,
    get_managed_learning_path,
    get_student_learning_path,
    get_student_learning_paths,
    list_learning_path_learners,
    list_managed_learning_paths,
    list_published_learning_paths,
    set_student_course_completion,
)


def form_data(**overrides):
    data = {
        "title": "Développeur web",
        "description": "Un parcours de développement web.",
        "theme": "web",
        "level": "beginner",
        "status": "draft",
        "course_1": "python-debutant",
        "course_2": "developpement-web",
    }
    data.update(overrides)
    return data


class LearningPathProposalTests(SimpleTestCase):
    def test_proposal_form_accepts_a_target_job_without_an_objective(self):
        form = LearningPathProposalForm({
            "objective": "",
            "target_job": "Développeur web junior",
            "level": "beginner",
        })

        self.assertTrue(form.is_valid(), form.errors)

    @patch("apps.learning_paths.ai.urlopen")
    def test_generator_returns_only_catalog_courses_as_a_draft(self, urlopen):
        generated = {
            "title": "Développeur web débutant",
            "description": "Un parcours pour apprendre les bases du web.",
            "theme": "web",
            "level": "beginner",
            "course_ids": ["python-debutant", "developpement-web"],
        }
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = json.dumps({
            "message": {"content": json.dumps(generated)},
        }).encode("utf-8")

        proposal = generate_learning_path_proposal(
            objective="Créer des sites web",
            target_job="Développeur web",
            level="beginner",
        )

        self.assertEqual(proposal["course_ids"], generated["course_ids"])
        self.assertEqual(proposal["status"], "draft")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, f"{settings.GROQ_BASE_URL.rstrip('/')}/chat/completions")
        self.assertEqual(request.headers.get("Authorization"), f"Bearer {settings.GROQ_API_KEY}")
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], settings.GROQ_MODEL)
        self.assertEqual(payload["response_format"]["type"], "json_object")

    @patch("apps.learning_paths.ai.urlopen")
    def test_generator_rejects_courses_outside_the_catalog(self, urlopen):
        generated = {
            "title": "Parcours invalide",
            "description": "Description.",
            "theme": "web",
            "level": "beginner",
            "course_ids": ["python-debutant", "course-invented-by-model"],
        }
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = json.dumps({
            "message": {"content": json.dumps(generated)},
        }).encode("utf-8")

        with self.assertRaises(LearningPathAIError):
            generate_learning_path_proposal(
                objective="Apprendre le web",
                target_job="",
                level="beginner",
            )

    @patch("apps.learning_paths.ai.urlopen")
    def test_statistics_insights_use_aggregate_data_and_return_recommendations(self, urlopen):
        generated = {
            "summary": "Les parcours attirent des apprenants, mais leur complétion peut progresser.",
            "recommendations": [
                "Ajoutez des étapes de suivi pour encourager la progression.",
                "Examinez les parcours les plus suivis pour en créer des variantes.",
            ],
        }
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = json.dumps({
            "message": {"content": json.dumps(generated)},
        }).encode("utf-8")

        insights = generate_learning_path_statistics_insights(statistics={
            "total_paths": 3,
            "published_paths": 2,
            "draft_paths": 1,
            "total_enrollments": 8,
            "completion_rate": 25,
            "average_progress": 48,
            "popular_paths": [{
                "title": "Développeur web",
                "theme_label": "Développement web",
                "level_label": "Débutant",
                "enrollment_count": 5,
            }],
        })

        self.assertEqual(insights, generated)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, f"{settings.GROQ_BASE_URL.rstrip('/')}/chat/completions")
        payload = json.loads(request.data)
        self.assertEqual(payload["response_format"]["type"], "json_object")
        prompt = payload["messages"][1]["content"]
        self.assertIn('"total_enrollments":8', prompt)
        self.assertNotIn("email", prompt)

    @patch("apps.learning_paths.ai.urlopen")
    def test_statistics_insights_reject_invalid_recommendation_shapes(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = json.dumps({
            "message": {
                "content": json.dumps({
                    "summary": "Analyse disponible.",
                    "recommendations": ["Une seule recommandation."],
                }),
            },
        }).encode("utf-8")

        with self.assertRaises(LearningPathAIError):
            generate_learning_path_statistics_insights(statistics={
                "total_paths": 0,
                "published_paths": 0,
                "draft_paths": 0,
                "total_enrollments": 0,
                "completion_rate": 0,
                "average_progress": 0,
                "popular_paths": [],
            })


class LearningPathPDFTests(SimpleTestCase):
    def test_pdf_paragraph_helpers_render_markup_and_escape_dynamic_text(self):
        style = getSampleStyleSheet()["BodyText"]

        formatted = _formatted_paragraph("<b>Apprenant</b>", style)
        escaped = _paragraph("<b>Nom apprenant</b>", style)

        self.assertEqual(formatted.getPlainText(), "Apprenant")
        self.assertEqual(escaped.getPlainText(), "<b>Nom apprenant</b>")

    def test_pdf_contains_a_valid_pdf_document(self):
        pdf_data = build_learning_path_pdf({
            "title": "Parcours développeur web",
            "description": "Apprendre à créer des sites web.",
            "courses": [
                {
                    "title": "Python pour débutants",
                    "description": "Les bases de Python.",
                    "detail": "12 modules",
                },
                {
                    "title": "Développement web",
                    "description": "Créer des interfaces.",
                    "detail": "18 modules",
                },
            ],
        })

        self.assertTrue(pdf_data.startswith(b"%PDF-"))
        self.assertTrue(pdf_data.rstrip().endswith(b"%%EOF"))

    def test_pdf_accepts_enrolled_learners_with_progress(self):
        pdf_data = build_learning_path_pdf(
            {
                "title": "Parcours développeur web",
                "description": "Apprendre à créer des sites web.",
                "courses": [
                    {"title": "Python pour débutants"},
                    {"title": "Développement web"},
                ],
            },
            learners=[{
                "name": "Ada Lovelace",
                "completed_count": 1,
                "total_courses": 2,
                "progress_percentage": 50,
                "enrolled_at": datetime(2026, 10, 24, tzinfo=timezone.utc),
            }],
        )

        self.assertTrue(pdf_data.startswith(b"%PDF-"))
        self.assertIn(b"/Count", pdf_data)
        self.assertTrue(pdf_data.rstrip().endswith(b"%%EOF"))


class LearningPathFormTests(SimpleTestCase):
    def test_course_order_is_preserved(self):
        form = LearningPathForm(form_data(
            course_1="developpement-web",
            course_2="python-debutant",
            course_3="bases-de-donnees",
        ))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.to_document()["course_ids"], [
            "developpement-web",
            "python-debutant",
            "bases-de-donnees",
        ])

    def test_at_least_two_courses_are_required(self):
        form = LearningPathForm(form_data(course_2=""))

        self.assertFalse(form.is_valid())
        self.assertIn("au moins deux cours", str(form.non_field_errors()))

    def test_duplicate_course_is_rejected(self):
        form = LearningPathForm(form_data(course_2="python-debutant"))

        self.assertFalse(form.is_valid())
        self.assertIn("une seule fois", str(form.non_field_errors()))

    def test_unknown_course_is_rejected(self):
        form = LearningPathForm(form_data(course_1="not-a-course"))

        self.assertFalse(form.is_valid())


class LearningPathServiceTests(SimpleTestCase):
    def setUp(self):
        self.author = {"_id": ObjectId(), "role": "instructor"}
        self.path_id = ObjectId()
        self.learning_path = {
            "_id": self.path_id,
            "title": "Développeur web",
            "description": "Un parcours.",
            "theme": "web",
            "level": "beginner",
            "status": "published",
            "course_ids": ["python-debutant", "developpement-web"],
            "created_by": self.author["_id"],
        }

    @patch("apps.learning_paths.services.repositories.list_learning_paths")
    def test_public_list_maps_course_references(self, list_paths):
        list_paths.return_value = [self.learning_path]

        result = list_published_learning_paths()

        list_paths.assert_called_once_with(published_only=True)
        self.assertEqual(
            [course["id"] for course in result[0]["courses"]],
            ["python-debutant", "developpement-web"],
        )

    @patch("apps.learning_paths.services.repositories.list_learning_paths")
    def test_public_search_filters_by_accent_insensitive_text(self, list_paths):
        list_paths.return_value = [self.learning_path]

        result = list_published_learning_paths(search="DÉVELOPPEMENT web")

        self.assertEqual([path["id"] for path in result], [str(self.path_id)])

    @patch("apps.learning_paths.services.repositories.list_learning_paths")
    def test_public_search_matches_terms_in_different_fields(self, list_paths):
        list_paths.return_value = [self.learning_path]

        result = list_published_learning_paths(search="python Exercices")

        self.assertEqual([path["id"] for path in result], [str(self.path_id)])

    @patch("apps.learning_paths.services.repositories.list_learning_paths")
    def test_public_filters_theme_and_level(self, list_paths):
        list_paths.return_value = [
            self.learning_path,
            {
                **self.learning_path,
                "_id": ObjectId(),
                "title": "Python",
                "theme": "programmation",
                "level": "advanced",
            },
        ]

        results = list_published_learning_paths(theme="web", level="beginner")

        self.assertEqual([path["title"] for path in results], ["Développeur web"])

    @patch("apps.learning_paths.services.repositories.list_learning_paths")
    def test_public_sort_orders_titles(self, list_paths):
        second_path = {
            **self.learning_path,
            "_id": ObjectId(),
            "title": "Bases de données",
        }
        list_paths.return_value = [self.learning_path, second_path]

        results = list_published_learning_paths(sort="title_asc")

        self.assertEqual(
            [path["title"] for path in results],
            ["Bases de données", "Développeur web"],
        )

    @patch("apps.learning_paths.services.repositories.count_enrollments", return_value={})
    @patch("apps.learning_paths.services.repositories.list_learning_paths")
    def test_instructor_only_lists_owned_paths(self, list_paths, count_enrollments):
        list_paths.return_value = []

        list_managed_learning_paths(self.author)

        list_paths.assert_called_once_with(created_by=self.author["_id"])

    @patch("apps.learning_paths.services.repositories.count_enrollments", return_value={})
    @patch("apps.learning_paths.services.repositories.list_learning_paths")
    def test_managed_list_filters_status_and_searches_titles(self, list_paths, count_enrollments):
        list_paths.return_value = [
            {**self.learning_path, "status": "published"},
            {
                **self.learning_path,
                "_id": ObjectId(),
                "title": "Python",
                "status": "draft",
            },
        ]

        results = list_managed_learning_paths(
            self.author,
            search="DÉVELOPPEMENT   PYTHON",
            status="published",
        )

        self.assertEqual([path["title"] for path in results], ["Développeur web"])

    @patch("apps.learning_paths.services.repositories.count_enrollments", return_value={})
    @patch("apps.learning_paths.services.repositories.list_learning_paths")
    def test_admin_lists_all_paths(self, list_paths, count_enrollments):
        list_paths.return_value = []

        list_managed_learning_paths({"_id": ObjectId(), "role": "admin"})

        list_paths.assert_called_once_with(created_by=None)

    @patch("apps.learning_paths.services.repositories.list_enrollments_for_paths")
    @patch("apps.learning_paths.services.repositories.list_learning_paths")
    def test_statistics_calculate_completion_and_average_for_owned_paths(
        self, list_paths, list_enrollments
    ):
        second_path_id = ObjectId()
        list_paths.return_value = [
            self.learning_path,
            {
                **self.learning_path,
                "_id": second_path_id,
                "title": "Bases de données",
                "status": "draft",
                "course_ids": ["python-debutant", "bases-de-donnees"],
            },
        ]
        list_enrollments.return_value = [
            {
                "learning_path_id": self.path_id,
                "completed_course_ids": ["python-debutant"],
            },
            {
                "learning_path_id": self.path_id,
                "completed_course_ids": ["python-debutant", "developpement-web"],
            },
            {
                "learning_path_id": second_path_id,
                "completed_course_ids": [],
            },
        ]

        stats = get_learning_path_statistics(self.author)

        list_paths.assert_called_once_with(created_by=self.author["_id"])
        list_enrollments.assert_called_once_with([self.path_id, second_path_id])
        self.assertEqual(stats["total_paths"], 2)
        self.assertEqual(stats["published_paths"], 1)
        self.assertEqual(stats["draft_paths"], 1)
        self.assertEqual(stats["total_enrollments"], 3)
        self.assertEqual(stats["completed_enrollments"], 1)
        self.assertEqual(stats["completion_rate"], 33)
        self.assertEqual(stats["average_progress"], 50)
        self.assertEqual(
            [(path["title"], path["enrollment_count"], path["enrollment_percentage"])
             for path in stats["popular_paths"]],
            [("Développeur web", 2, 100), ("Bases de données", 1, 50)],
        )

    @patch("apps.learning_paths.services.repositories.list_enrollments_for_paths")
    @patch("apps.learning_paths.services.repositories.list_learning_paths")
    def test_admin_statistics_include_all_paths(self, list_paths, list_enrollments):
        admin = {"_id": ObjectId(), "role": "admin"}
        list_paths.return_value = []
        list_enrollments.return_value = []

        stats = get_learning_path_statistics(admin)

        list_paths.assert_called_once_with(created_by=None)
        list_enrollments.assert_called_once_with([])
        self.assertEqual(stats["total_enrollments"], 0)
        self.assertEqual(stats["completion_rate"], 0)
        self.assertEqual(stats["average_progress"], 0)
        self.assertEqual(stats["popular_paths"], [])

    @patch("apps.learning_paths.services.repositories.get_learning_path")
    def test_instructor_cannot_manage_another_authors_path(self, get_path):
        get_path.return_value = {**self.learning_path, "created_by": ObjectId()}

        with self.assertRaises(LearningPathPermissionError):
            get_managed_learning_path(str(self.path_id), self.author)

    @patch("apps.learning_paths.services.repositories.insert_learning_path")
    def test_create_records_author_and_timestamps(self, insert_path):
        insert_path.return_value = self.path_id
        data = {
            "title": "Développeur web",
            "description": "Un parcours.",
            "status": "draft",
            "course_ids": ["python-debutant", "developpement-web"],
        }

        result = create_learning_path(data=data, user=self.author)

        self.assertEqual(result, self.path_id)
        document = insert_path.call_args.args[0]
        self.assertEqual(document["created_by"], self.author["_id"])
        self.assertEqual(document["course_ids"], data["course_ids"])
        self.assertIsInstance(document["created_at"], datetime)
        self.assertEqual(document["created_at"].tzinfo, timezone.utc)
        self.assertEqual(document["updated_at"], document["created_at"])

    @patch("apps.learning_paths.services.repositories.enroll_student", return_value=True)
    @patch("apps.learning_paths.services.repositories.get_learning_path")
    def test_student_can_enroll_only_in_a_published_path(self, get_path, enroll_student):
        get_path.return_value = self.learning_path

        created = enroll_in_learning_path(str(self.path_id), self.author)

        self.assertTrue(created)
        enroll_student.assert_called_once()

        get_path.return_value = {**self.learning_path, "status": "draft"}
        with self.assertRaises(LearningPathNotFoundError):
            enroll_in_learning_path(str(self.path_id), self.author)

    @patch("apps.learning_paths.services.repositories.list_student_enrollments")
    @patch("apps.learning_paths.services.repositories.get_learning_path")
    def test_student_path_list_contains_current_progress(self, get_path, list_enrollments):
        list_enrollments.return_value = [{
            "learning_path_id": self.path_id,
            "user_id": self.author["_id"],
            "completed_course_ids": ["python-debutant"],
            "enrolled_at": datetime.now(timezone.utc),
        }]
        get_path.return_value = self.learning_path

        paths = get_student_learning_paths(self.author)

        self.assertEqual(paths[0]["progress_percentage"], 50)
        self.assertEqual(paths[0]["completed_count"], 1)
        self.assertEqual(paths[0]["total_courses"], 2)

    @patch("apps.learning_paths.services.repositories.get_enrollment")
    @patch("apps.learning_paths.services.repositories.get_learning_path")
    def test_student_detail_requires_enrollment_and_displays_course_states(
        self, get_path, get_enrollment
    ):
        get_path.return_value = self.learning_path
        get_enrollment.return_value = {
            "completed_course_ids": ["python-debutant"],
            "enrolled_at": datetime.now(timezone.utc),
        }

        result = get_student_learning_path(str(self.path_id), self.author)

        self.assertEqual(result["progress_percentage"], 50)
        self.assertTrue(result["courses"][0]["is_completed"])
        self.assertFalse(result["courses"][1]["is_completed"])

        get_enrollment.return_value = None
        with self.assertRaises(LearningPathEnrollmentRequiredError):
            get_student_learning_path(str(self.path_id), self.author)

    @patch("apps.learning_paths.services.repositories.set_course_completion", return_value=True)
    @patch("apps.learning_paths.services.repositories.get_enrollment", return_value={"completed_course_ids": []})
    @patch("apps.learning_paths.services.repositories.get_learning_path")
    def test_student_can_complete_only_courses_in_an_enrolled_path(
        self, get_path, get_enrollment, set_completion
    ):
        get_path.return_value = self.learning_path

        set_student_course_completion(
            str(self.path_id),
            "python-debutant",
            user=self.author,
            completed=True,
        )

        set_completion.assert_called_once()
        with self.assertRaises(LearningPathNotFoundError):
            set_student_course_completion(
                str(self.path_id),
                "not-a-course",
                user=self.author,
                completed=True,
            )

        get_enrollment.return_value = None
        with self.assertRaises(LearningPathEnrollmentRequiredError):
            set_student_course_completion(
                str(self.path_id),
                "python-debutant",
                user=self.author,
                completed=True,
            )

    @patch("apps.learning_paths.services.user_repositories.find_active_users")
    @patch("apps.learning_paths.services.repositories.list_path_enrollments")
    @patch("apps.learning_paths.services.repositories.get_learning_path")
    def test_instructor_sees_enrolled_learner_progress(
        self, get_path, list_enrollments, find_users
    ):
        learner_id = ObjectId()
        get_path.return_value = self.learning_path
        list_enrollments.return_value = [{
            "user_id": learner_id,
            "completed_course_ids": ["python-debutant"],
            "enrolled_at": datetime.now(timezone.utc),
        }]
        find_users.return_value = [{
            "_id": learner_id,
            "first_name": "Ada",
            "last_name": "Lovelace",
        }]

        result = list_learning_path_learners(str(self.path_id), self.author)

        self.assertEqual(result["learners"][0]["name"], "Ada Lovelace")
        self.assertEqual(result["learners"][0]["progress_percentage"], 50)

    @patch("apps.learning_paths.services.repositories.list_path_enrollments")
    @patch("apps.learning_paths.services.repositories.get_learning_path")
    def test_other_instructor_cannot_view_learner_progress(self, get_path, list_enrollments):
        get_path.return_value = {**self.learning_path, "created_by": ObjectId()}

        with self.assertRaises(LearningPathPermissionError):
            list_learning_path_learners(str(self.path_id), self.author)

        list_enrollments.assert_not_called()


@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class LearningPathViewsTests(SimpleTestCase):
    def setUp(self):
        self.user = {
            "_id": ObjectId(),
            "first_name": "Ada",
            "last_name": "Lovelace",
            "role": "instructor",
        }
        self.path_id = ObjectId()
        session = self.client.session
        session["user_id"] = str(self.user["_id"])
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key

    def test_public_routes_and_manage_routes_reverse(self):
        self.assertEqual(reverse("learning_paths:list"), "/parcours/")
        self.assertEqual(reverse("learning_paths:student_list"), "/parcours/mes-parcours/")
        self.assertEqual(
            reverse("learning_paths:enroll", kwargs={"path_id": str(self.path_id)}),
            f"/parcours/{self.path_id}/inscription/",
        )
        self.assertEqual(
            reverse("learning_paths:progress", kwargs={"path_id": str(self.path_id)}),
            f"/parcours/{self.path_id}/progression/",
        )
        self.assertEqual(
            reverse("learning_paths:manage_list"),
            "/parcours/manage/",
        )
        self.assertEqual(
            reverse("learning_paths:manage_statistics"),
            "/parcours/manage/statistics/",
        )
        self.assertEqual(
            reverse("learning_paths:manage_generate"),
            "/parcours/manage/generate/",
        )
        self.assertEqual(
            reverse("learning_paths:export_pdf", kwargs={"path_id": str(self.path_id)}),
            f"/parcours/{self.path_id}/export.pdf",
        )
        self.assertEqual(
            reverse("learning_paths:manage_export_pdf", kwargs={"path_id": str(self.path_id)}),
            f"/parcours/manage/{self.path_id}/export.pdf",
        )
        self.assertEqual(
            reverse("learning_paths:detail", kwargs={"path_id": str(self.path_id)}),
            f"/parcours/{self.path_id}/",
        )

    def test_public_list_and_detail_render_published_paths(self):
        learning_path = {
            "id": str(self.path_id),
            "title": "Développeur web",
            "description": "Deux cours pour commencer.",
            "theme_label": "Développement web",
            "level_label": "Débutant",
            "status": "published",
            "courses": [
                {"title": "Python", "description": "Bases.", "detail": "12 modules"},
                {"title": "Web", "description": "Interfaces.", "detail": "18 modules"},
            ],
        }
        with patch.object(views, "list_published_learning_paths", return_value=[learning_path]), \
             patch.object(views, "get_published_learning_path", return_value=learning_path):
            list_response = self.client.get(reverse("learning_paths:list"))
            detail_response = self.client.get(reverse(
                "learning_paths:detail", kwargs={"path_id": str(self.path_id)}
            ))

        self.assertContains(list_response, "Développeur web")
        self.assertContains(list_response, 'href="/parcours/"')
        self.assertContains(list_response, 'href="/users/cours/"')
        self.assertContains(list_response, 'href="/users/mentors/"')
        self.assertContains(detail_response, "Python")
        self.assertContains(detail_response, "Web")
        self.assertContains(detail_response, 'href="/parcours/"')
        self.assertContains(detail_response, "Télécharger le parcours en PDF")

    def test_course_page_has_the_same_always_available_learning_path_link(self):
        response = self.client.get(reverse("users:courses"))

        self.assertContains(response, 'href="/parcours/"')

    def test_public_list_applies_query_filters_and_displays_filter_controls(self):
        with patch.object(views, "list_published_learning_paths", return_value=[]) as list_paths:
            response = self.client.get(
                reverse("learning_paths:list"),
                {"q": "python", "theme": "web", "level": "beginner", "sort": "title_asc"},
            )

        list_paths.assert_called_once_with(
            search="python",
            theme="web",
            level="beginner",
            sort="title_asc",
        )
        self.assertContains(response, 'name="q"')
        self.assertContains(response, 'name="theme"')
        self.assertContains(response, 'name="level"')
        self.assertContains(response, 'name="sort"')

    def test_management_list_applies_query_filters_and_displays_controls(self):
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(views, "list_managed_learning_paths", return_value=[]) as list_paths:
            response = self.client.get(
                reverse("learning_paths:manage_list"),
                {"q": "python", "status": "draft", "sort": "oldest"},
            )

        list_paths.assert_called_once_with(
            self.user,
            search="python",
            status="draft",
            sort="oldest",
        )
        self.assertContains(response, 'name="q"')
        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'name="sort"')
        content = response.content.decode()
        courses_panel = content.index('class="panel courses-panel"')
        search_form = content.index('class="learning-path-filters"')
        self.assertGreater(search_form, courses_panel)

    def test_management_list_links_to_statistics_dashboard(self):
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(views, "list_managed_learning_paths", return_value=[]):
            response = self.client.get(reverse("learning_paths:manage_list"))

        self.assertContains(
            response,
            reverse("learning_paths:manage_statistics"),
        )

    def test_instructor_can_view_scoped_statistics_dashboard(self):
        statistics = {
            "total_paths": 2,
            "published_paths": 1,
            "draft_paths": 1,
            "total_enrollments": 3,
            "completed_enrollments": 1,
            "completion_rate": 33,
            "average_progress": 50,
            "popular_paths": [{
                "id": str(self.path_id),
                "title": "Développeur web",
                "theme_label": "Développement web",
                "level_label": "Débutant",
                "courses": [{}, {}],
                "enrollment_count": 3,
                "enrollment_percentage": 100,
            }],
        }
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(
                 views,
                 "get_learning_path_statistics",
                 return_value=statistics,
             ) as get_statistics:
            response = self.client.get(reverse("learning_paths:manage_statistics"))

        self.assertContains(response, "Statistiques des parcours")
        self.assertContains(response, "33%")
        self.assertContains(response, "50%")
        self.assertContains(response, "Développeur web")
        get_statistics.assert_called_once_with(self.user)

    def test_instructor_can_request_ai_statistics_recommendations(self):
        statistics = {
            "total_paths": 2,
            "published_paths": 1,
            "draft_paths": 1,
            "total_enrollments": 3,
            "completed_enrollments": 1,
            "completion_rate": 33,
            "average_progress": 50,
            "popular_paths": [],
        }
        insights = {
            "summary": "La complétion peut progresser.",
            "recommendations": [
                "Ajoutez des jalons intermédiaires.",
                "Présentez clairement les objectifs des cours.",
            ],
        }
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(
                 views,
                 "get_learning_path_statistics",
                 return_value=statistics,
             ), \
             patch.object(
                 views,
                 "generate_learning_path_statistics_insights",
                 return_value=insights,
             ) as generate_insights:
            response = self.client.post(
                reverse("learning_paths:manage_statistics"),
            )

        self.assertContains(response, "La complétion peut progresser.")
        self.assertContains(response, "Ajoutez des jalons intermédiaires.")
        generate_insights.assert_called_once_with(statistics=statistics)

    def test_statistics_dashboard_reports_ai_service_errors(self):
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(views, "get_learning_path_statistics", return_value={
                 "total_paths": 0,
                 "published_paths": 0,
                 "draft_paths": 0,
                 "total_enrollments": 0,
                 "completed_enrollments": 0,
                 "completion_rate": 0,
                 "average_progress": 0,
                 "popular_paths": [],
             }), \
             patch.object(
                 views,
                 "generate_learning_path_statistics_insights",
                 side_effect=LearningPathAIError("Ollama est inaccessible."),
             ):
            response = self.client.post(
                reverse("learning_paths:manage_statistics"),
            )

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Ollama est inaccessible.", status_code=503)

    def test_student_cannot_view_statistics_dashboard(self):
        student = {**self.user, "role": "student"}
        with patch("apps.users.decorators.get_active_user", return_value=student):
            response = self.client.get(reverse("learning_paths:manage_statistics"))

        self.assertEqual(response.status_code, 403)

    def test_student_can_enroll_and_open_progress_page(self):
        student = {**self.user, "role": "student"}
        with patch("apps.users.decorators.get_active_user", return_value=student), \
             patch.object(views, "enroll_in_learning_path", return_value=True) as enroll:
            response = self.client.post(
                reverse("learning_paths:enroll", kwargs={"path_id": str(self.path_id)})
            )

        self.assertRedirects(
            response,
            reverse("learning_paths:student_detail", kwargs={"path_id": str(self.path_id)}),
            fetch_redirect_response=False,
        )
        enroll.assert_called_once()

        expected_path = {
            "id": str(self.path_id),
            "title": "Développeur web",
            "description": "Deux cours.",
            "courses": [
                {"id": "python-debutant", "title": "Python", "description": "Bases.",
                 "detail": "12 modules", "is_completed": False},
                {"id": "developpement-web", "title": "Web", "description": "Interfaces.",
                 "detail": "18 modules", "is_completed": True},
            ],
            "completed_count": 1,
            "total_courses": 2,
            "progress_percentage": 50,
            "is_complete": False,
        }
        with patch("apps.users.decorators.get_active_user", return_value=student), \
             patch.object(views, "get_student_learning_path", return_value=expected_path):
            detail = self.client.get(reverse(
                "learning_paths:student_detail",
                kwargs={"path_id": str(self.path_id)},
            ))

        self.assertContains(detail, "50%")
        self.assertContains(detail, "Marquer comme terminé")
        self.assertContains(detail, "Marquer comme non terminé")

    def test_progress_endpoint_updates_course_and_redirects_to_tracking_detail(self):
        student = {**self.user, "role": "student"}
        with patch("apps.users.decorators.get_active_user", return_value=student), \
             patch.object(views, "set_student_course_completion") as set_completion:
            response = self.client.post(
                reverse("learning_paths:progress", kwargs={"path_id": str(self.path_id)}),
                {"course_id": "python-debutant", "completed": "true"},
            )

        set_completion.assert_called_once_with(
            str(self.path_id),
            "python-debutant",
            user=student,
            completed=True,
        )
        self.assertRedirects(
            response,
            reverse("learning_paths:student_detail", kwargs={"path_id": str(self.path_id)}),
            fetch_redirect_response=False,
        )

    def test_student_can_open_personal_progress_list(self):
        student = {**self.user, "role": "student"}
        paths = [{
            "id": str(self.path_id),
            "title": "Développeur web",
            "description": "Deux cours.",
            "progress_percentage": 50,
            "completed_count": 1,
            "total_courses": 2,
            "is_complete": False,
        }]
        with patch("apps.users.decorators.get_active_user", return_value=student), \
             patch.object(views, "get_student_learning_paths", return_value=paths):
            response = self.client.get(reverse("learning_paths:student_list"))

        self.assertContains(response, "Développeur web")
        self.assertContains(response, "50%")

    def test_management_list_links_to_learner_tracking(self):
        path = {
            "id": str(self.path_id),
            "title": "Développeur web",
            "description": "Deux cours.",
            "status": "published",
            "courses": [],
            "enrollment_count": 2,
        }
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(views, "list_managed_learning_paths", return_value=[path]):
            response = self.client.get(reverse("learning_paths:manage_list"))

        self.assertContains(
            response,
            reverse("learning_paths:manage_learners", kwargs={"path_id": str(self.path_id)}),
        )
        self.assertContains(
            response,
            reverse("learning_paths:manage_export_pdf", kwargs={"path_id": str(self.path_id)}),
        )
        self.assertContains(response, "2 apprenant(s)")

    def test_instructor_can_generate_and_review_a_draft_proposal(self):
        proposal = {
            "title": "Développeur web débutant",
            "description": "Apprendre les bases du web.",
            "theme": "web",
            "level": "beginner",
            "status": "draft",
            "course_ids": ["python-debutant", "developpement-web"],
        }
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(
                 views,
                 "generate_learning_path_proposal",
                 return_value=proposal,
             ) as generate:
            response = self.client.post(
                reverse("learning_paths:manage_generate"),
                {
                    "objective": "Créer des sites web",
                    "target_job": "Développeur web junior",
                    "level": "beginner",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vérifier la proposition IA")
        self.assertContains(response, 'action="/parcours/manage/create/"')
        self.assertEqual(response.context["form"]["title"].value(), proposal["title"])
        self.assertEqual(
            response.context["form"]["course_2"].value(),
            "developpement-web",
        )
        generate.assert_called_once_with(
            objective="Créer des sites web",
            target_job="Développeur web junior",
            level="beginner",
        )

    def test_generation_form_displays_wait_feedback(self):
        with patch("apps.users.decorators.get_active_user", return_value=self.user):
            response = self.client.get(reverse("learning_paths:manage_generate"))

        self.assertContains(response, "Génération en cours")
        self.assertContains(response, 'aria-live="polite"')

    def test_generation_reports_ollama_connection_errors(self):
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(
                 views,
                 "generate_learning_path_proposal",
                 side_effect=LearningPathAIError("Ollama est inaccessible."),
             ):
            response = self.client.post(
                reverse("learning_paths:manage_generate"),
                {"objective": "Apprendre Python", "target_job": "", "level": "beginner"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Ollama est inaccessible.", status_code=503)

    def test_instructor_can_open_learner_progress_for_owned_path(self):
        context = {
            "learning_path": {
                "id": str(self.path_id),
                "title": "Développeur web",
            },
            "learners": [{
                "name": "Ada Lovelace",
                "progress_percentage": 50,
                "completed_count": 1,
                "total_courses": 2,
                "is_complete": False,
            }],
        }
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(views, "list_learning_path_learners", return_value=context):
            response = self.client.get(reverse(
                "learning_paths:manage_learners",
                kwargs={"path_id": str(self.path_id)},
            ))

        self.assertContains(response, "Ada Lovelace")
        self.assertContains(response, "50%")

    def test_public_export_downloads_only_published_path_as_pdf(self):
        path = {
            "id": str(self.path_id),
            "title": "Développeur web",
            "description": "Deux cours.",
            "courses": [
                {"title": "Python", "description": "Bases."},
                {"title": "Web", "description": "Interfaces."},
            ],
        }
        pdf_bytes = b"%PDF-test"
        with patch.object(views, "get_published_learning_path", return_value=path), \
             patch.object(views, "build_learning_path_pdf", return_value=pdf_bytes) as build_pdf:
            response = self.client.get(reverse(
                "learning_paths:export_pdf",
                kwargs={"path_id": str(self.path_id)},
            ))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Content-Disposition"], (
            f'attachment; filename="parcours-{self.path_id}.pdf"'
        ))
        self.assertEqual(response.content, pdf_bytes)
        build_pdf.assert_called_once_with(path, learners=None)

    def test_instructor_can_export_owned_draft_as_pdf(self):
        path = {
            "id": str(self.path_id),
            "title": "Parcours brouillon",
            "description": "Étapes ordonnées.",
            "courses": [],
        }
        learners = [{
            "name": "Ada Lovelace",
            "completed_count": 1,
            "total_courses": 2,
            "progress_percentage": 50,
        }]
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(
                 views,
                 "list_learning_path_learners",
                 return_value={"learning_path": path, "learners": learners},
             ) as get_learners, \
             patch.object(views, "build_learning_path_pdf", return_value=b"%PDF-test") as build_pdf:
            response = self.client.get(reverse(
                "learning_paths:manage_export_pdf",
                kwargs={"path_id": str(self.path_id)},
            ))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        get_learners.assert_called_once_with(str(self.path_id), self.user)
        build_pdf.assert_called_once_with(path, learners=learners)

    def test_instructor_cannot_export_another_users_path(self):
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(
                 views,
                 "list_learning_path_learners",
                 side_effect=LearningPathPermissionError,
             ):
            response = self.client.get(reverse(
                "learning_paths:manage_export_pdf",
                kwargs={"path_id": str(self.path_id)},
            ))

        self.assertEqual(response.status_code, 403)

    def test_instructor_can_create_a_learning_path(self):
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(views, "create_learning_path", return_value=self.path_id) as create:
            response = self.client.post(reverse("learning_paths:manage_create"), form_data())

        self.assertRedirects(
            response,
            reverse("learning_paths:manage_list"),
            fetch_redirect_response=False,
        )
        create.assert_called_once()

    def test_management_sidebar_has_no_create_link_but_page_keeps_create_action(self):
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(views, "list_managed_learning_paths", return_value=[]):
            response = self.client.get(reverse("learning_paths:manage_list"))

        self.assertContains(response, "Mes parcours")
        self.assertContains(response, "Créer un parcours")
        self.assertEqual(
            response.content.count(b'href="/parcours/manage/create/"'),
            1,
        )

    def test_student_cannot_open_management_routes(self):
        student = {**self.user, "role": "student"}
        with patch("apps.users.decorators.get_active_user", return_value=student):
            response = self.client.get(reverse("learning_paths:manage_list"))

        self.assertEqual(response.status_code, 403)

    def test_instructor_can_update_and_delete_owned_path(self):
        learning_path = {
            "_id": self.path_id,
            "id": str(self.path_id),
            "title": "Développeur web",
            "description": "Deux cours.",
            "status": "draft",
            "course_ids": ["python-debutant", "developpement-web"],
            "created_by": self.user["_id"],
            "courses": [],
        }
        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(views, "get_managed_learning_path", return_value=learning_path), \
             patch.object(views, "update_learning_path") as update:
            response = self.client.post(
                reverse("learning_paths:manage_edit", kwargs={"path_id": str(self.path_id)}),
                form_data(status="published"),
            )
        self.assertRedirects(
            response,
            reverse("learning_paths:manage_list"),
            fetch_redirect_response=False,
        )
        update.assert_called_once()

        with patch("apps.users.decorators.get_active_user", return_value=self.user), \
             patch.object(views, "get_managed_learning_path", return_value=learning_path), \
             patch.object(views, "delete_learning_path") as delete:
            response = self.client.post(
                reverse("learning_paths:manage_delete", kwargs={"path_id": str(self.path_id)})
            )
        self.assertRedirects(
            response,
            reverse("learning_paths:manage_list"),
            fetch_redirect_response=False,
        )
        delete.assert_called_once()
