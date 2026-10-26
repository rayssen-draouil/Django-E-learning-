from contextlib import ExitStack
from unittest.mock import patch

from bson import ObjectId
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse
from pymongo.errors import PyMongoError

from apps.courses import repository
from apps.users.services import UserStorageError

from .base import MongoTestCase, course_document, user_document


def course_payload(**overrides):
    data = {
        "title": "Nouveau cours",
        "slug": "",
        "description": "Une description suffisamment détaillée pour le formulaire.",
        "category": "web",
        "level": "intermediate",
        "is_free": "on",
        "price": "",
        "thumbnail": "",
        "status": "draft",
    }
    data.update(overrides)
    return data


@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class CourseViewTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.owner = user_document(role="instructor", email="owner@example.com")
        self.other = user_document(
            role="instructor", first_name="Grace", last_name="Hopper",
            email="grace@example.com",
        )
        self.admin = user_document(role="admin", email="admin@example.com")
        self.student = user_document(role="student", email="student@example.com")
        self.published = course_document(
            slug="python-publie",
            title="Python publié",
            description="Un cours publié visible par tous.",
            status="published",
            instructor_id=self.owner["_id"],
            instructor_name="Ada Lovelace",
            image="courses/python.png",
            image_alt="Illustration du cours Python publié",
            thumbnail="courses/python.png",
        )
        self.draft = course_document(
            slug="python-brouillon",
            title="Brouillon Python",
            description="Un brouillon privé.",
            status="draft",
            instructor_id=self.owner["_id"],
            instructor_name="Ada Lovelace",
        )
        self.other_published = course_document(
            slug="design-ux",
            title="Design UX",
            description="Un cours de design publié.",
            category="design",
            level="advanced",
            status="published",
            instructor_id=self.other["_id"],
            instructor_name="Grace Hopper",
        )
        self.collection.insert_many([self.published, self.draft, self.other_published])

    def _authenticate(self, client, user):
        session = client.session
        session["user_id"] = str(user["_id"])
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("apps.users.decorators.get_active_user", return_value=user))
        stack.enter_context(patch("apps.courses.views.get_active_user", return_value=user))
        return client

    def _csrf_token(self, client, url):
        response = client.get(url)
        self.assertIn(response.status_code, (200, 403, 405))
        token = client.cookies.get("csrftoken")
        self.assertIsNotNone(token)
        return token.value

    def test_public_catalogue_lists_published_courses_and_hides_drafts(self):
        response = self.client.get(reverse("users:courses"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Python publié")
        self.assertContains(response, "Design UX")
        self.assertNotContains(response, "Brouillon Python")
        self.assertContains(response, "Illustration du cours Python publié")
        self.assertEqual(response.content.count(b"<h1"), 1)

    def test_public_catalogue_supports_search_category_and_level_filters(self):
        url = reverse("users:courses")
        response = self.client.get(url, {"q": "design"})
        self.assertContains(response, "Design UX")
        self.assertNotContains(response, "Python publié")

        response = self.client.get(url, {"category": "programming"})
        self.assertContains(response, "Python publié")
        self.assertNotContains(response, "Design UX")

        response = self.client.get(url, {"level": "advanced"})
        self.assertContains(response, "Design UX")
        self.assertNotContains(response, "Python publié")

        response = self.client.get(url, {"q": "introuvable"})
        self.assertContains(response, "No courses found")

    def test_public_catalogue_supports_recent_title_price_and_duration_sorting(self):
        self.collection.delete_many({})
        self.collection.insert_many([
            course_document(
                slug="alpha", title="Alpha", status="published", price=30,
                duration_hours=5, instructor_id=self.owner["_id"],
            ),
            course_document(
                slug="beta", title="Beta", status="published", price=10,
                duration_hours=20, instructor_id=self.owner["_id"],
            ),
            course_document(
                slug="gamma", title="Gamma", status="published", price=20,
                duration_hours=10, instructor_id=self.owner["_id"],
            ),
        ])
        url = reverse("users:courses")
        expected = {
            "title": ["Alpha", "Beta", "Gamma"],
            "price_asc": ["Beta", "Gamma", "Alpha"],
            "duration_asc": ["Alpha", "Gamma", "Beta"],
        }
        for sort_by, titles in expected.items():
            with self.subTest(sort_by=sort_by):
                response = self.client.get(url, {"sort": sort_by})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["sort_by"], sort_by)
                self.assertEqual(
                    [course["title"] for course in response.context["courses"]], titles
                )
        for label in ("Plus récents", "Titre, de A à Z", "Prix croissant", "Durée croissante"):
            self.assertContains(response, label)

        invalid = self.client.get(url, {"sort": "inconnu"})
        self.assertEqual(invalid.context["sort_by"], "recent")
        self.assertEqual(invalid.status_code, 200)

    def test_public_catalogue_paginates_results(self):
        extra = [
            course_document(
                slug=f"cours-{index}",
                title=f"Cours numéro {index}",
                status="published",
                instructor_id=self.owner["_id"],
            )
            for index in range(1, 9)
        ]
        self.collection.insert_many(extra)
        url = reverse("users:courses")
        response = self.client.get(url)
        self.assertContains(response, "Page 1 of 2")
        self.assertContains(response, "Next page")
        response = self.client.get(url, {"page": 2})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Page 2 of 2")
        self.assertContains(response, "Previous page")
        self.assertNotContains(response, "Next page")
        self.assertEqual(self.client.get(url, {"page": "invalid"}).status_code, 200)

    def test_published_detail_is_public_but_drafts_are_private(self):
        detail_url = reverse("users:course_detail", args=[self.published["slug"]])
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Python publié")
        self.assertContains(response, "Ada Lovelace")
        self.assertEqual(response.content.count(b"<h1"), 1)

        draft_url = reverse("users:course_detail", args=[self.draft["slug"]])
        self.assertEqual(self.client.get(draft_url).status_code, 404)

        self._authenticate(self.client, self.student)
        self.assertEqual(self.client.get(draft_url).status_code, 404)

        owner_client = self._authenticate(Client(), self.owner)
        response = owner_client.get(draft_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft")

        admin_client = self._authenticate(Client(), self.admin)
        self.assertEqual(admin_client.get(draft_url).status_code, 200)

    def test_unknown_slug_and_invalid_object_ids_return_404(self):
        self.assertEqual(
            self.client.get(reverse("users:course_detail", args=["inconnu"])).status_code, 404
        )
        client = self._authenticate(Client(), self.owner)
        self.assertEqual(
            client.get(reverse("users:course_manage_detail", args=["invalid-id"])).status_code, 404
        )
        self.assertEqual(
            client.get(reverse("users:course_manage_edit", args=["invalid-id"])).status_code, 404
        )
        self.assertEqual(
            client.get(reverse("users:course_manage_delete", args=["invalid-id"])).status_code, 404
        )

    def test_manage_routes_require_authentication_and_a_manager_role(self):
        protected = (
            reverse("users:course_manage_list"),
            reverse("users:course_manage_create"),
            reverse("users:course_manage_detail", args=[str(self.draft["_id"])]),
            reverse("users:course_manage_edit", args=[str(self.draft["_id"])]),
            reverse("users:course_manage_delete", args=[str(self.draft["_id"])]),
        )
        for url in protected:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)

        self._authenticate(self.client, self.student)
        for url in protected:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("users:course_manage_publish", args=[str(self.draft["_id"])])
            ).status_code,
            403,
        )

    def test_manage_list_shows_only_the_managers_courses(self):
        client = self._authenticate(Client(), self.owner)
        response = client.get(reverse("users:course_manage_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Python publié")
        self.assertContains(response, "Brouillon Python")
        self.assertNotContains(response, "Design UX")
        self.assertEqual(response.content.count(b"<h1"), 1)

        other_client = self._authenticate(Client(), self.other)
        response = other_client.get(reverse("users:course_manage_list"))
        self.assertContains(response, "Design UX")
        self.assertNotContains(response, "Brouillon Python")

        admin_client = self._authenticate(Client(), self.admin)
        response = admin_client.get(reverse("users:course_manage_list"))
        self.assertContains(response, "Python publié")
        self.assertContains(response, "Design UX")

    def test_non_owner_instructors_cannot_edit_or_delete_other_courses(self):
        client = self._authenticate(Client(enforce_csrf_checks=True), self.other)
        detail_url = reverse("users:course_manage_detail", args=[str(self.draft["_id"])])
        edit_url = reverse("users:course_manage_edit", args=[str(self.draft["_id"])])
        delete_url = reverse("users:course_manage_delete", args=[str(self.draft["_id"])])
        publish_url = reverse("users:course_manage_publish", args=[str(self.draft["_id"])])
        self.assertEqual(client.get(detail_url).status_code, 403)
        self.assertEqual(client.get(edit_url).status_code, 403)
        self.assertEqual(client.get(delete_url).status_code, 403)
        token = self._csrf_token(client, reverse("users:course_manage_create"))
        self.assertEqual(client.post(edit_url, course_payload(), HTTP_X_CSRFTOKEN=token).status_code, 403)
        self.assertEqual(client.post(delete_url, {}, HTTP_X_CSRFTOKEN=token).status_code, 403)
        self.assertEqual(client.post(publish_url, {}, HTTP_X_CSRFTOKEN=token).status_code, 403)
        self.assertIsNotNone(repository.get_course(self.draft["_id"]))
        self.assertEqual(repository.get_course(self.draft["_id"])["title"], "Brouillon Python")

    def test_admin_can_manage_another_instructors_course(self):
        client = self._authenticate(Client(), self.admin)
        detail_url = reverse("users:course_manage_detail", args=[str(self.other_published["_id"])])
        response = client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Design UX")
        edit_url = reverse("users:course_manage_edit", args=[str(self.other_published["_id"])])
        response = client.get(edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Design UX")

    def test_management_detail_shows_draft_and_actions(self):
        client = self._authenticate(Client(), self.owner)
        response = client.get(
            reverse("users:course_manage_detail", args=[str(self.draft["_id"])])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Brouillon Python")
        self.assertContains(response, "Publish")
        self.assertContains(response, "Edit")
        self.assertContains(response, "Delete")
        self.assertEqual(response.content.count(b"<h1"), 1)

    def test_legacy_course_urls_redirect_to_the_management_routes(self):
        self.assertEqual(
            reverse("users:course_manage_list"), "/users/backoffice/cours/manage/"
        )
        self.assertEqual(
            reverse("users:course_manage_create"), "/users/backoffice/cours/create/"
        )
        self.assertEqual(
            reverse("courses:course_manage_create"), "/backoffice/cours/create/"
        )
        self.assertEqual(
            reverse("courses:course_manage_detail", args=["abc"]),
            "/backoffice/cours/abc/",
        )
        list_response = self.client.get(reverse("users:course_manage_list_legacy"))
        self.assertRedirects(
            list_response,
            reverse("users:course_manage_list"),
            status_code=302,
            target_status_code=302,
            fetch_redirect_response=False,
        )
        create_response = self.client.get(reverse("users:course_manage_create_legacy"))
        self.assertRedirects(
            create_response,
            reverse("users:course_manage_create"),
            status_code=302,
            target_status_code=302,
            fetch_redirect_response=False,
        )

    def test_owner_can_create_a_course_through_the_form(self):
        client = self._authenticate(Client(enforce_csrf_checks=True), self.owner)
        create_url = reverse("users:course_manage_create")
        response = client.get(create_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'for="id_title"')
        self.assertContains(response, 'id="id_title_help"')
        self.assertContains(response, 'aria-describedby="id_title_help"')
        self.assertEqual(response.content.count(b"<h1"), 1)
        token = client.cookies["csrftoken"].value

        response = client.post(create_url, course_payload(), HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 302)
        created = repository.get_course_by_slug("nouveau-cours")
        self.assertIsNotNone(created)
        self.assertEqual(created["instructor_id"], self.owner["_id"])
        self.assertEqual(created["status"], "draft")
        self.assertEqual(created["price"], 0.0)
        detail_url = reverse("users:course_manage_detail", args=[str(created["_id"])])
        self.assertRedirects(response, detail_url, fetch_redirect_response=False)
        response = client.get(detail_url)
        self.assertContains(response, "Le cours a été créé.")

    def test_create_form_reports_validation_errors_with_accessible_markup(self):
        client = self._authenticate(Client(), self.owner)
        response = client.post(
            reverse("users:course_manage_create"), course_payload(title="", description="")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="id_title_error"')
        self.assertIsNone(repository.get_course_by_slug("nouveau-cours"))

    def test_owner_can_edit_a_course_and_admin_can_edit_any_course(self):
        client = self._authenticate(Client(enforce_csrf_checks=True), self.owner)
        edit_url = reverse("users:course_manage_edit", args=[str(self.published["_id"])])
        response = client.get(edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Python publié")
        token = client.cookies["csrftoken"].value
        payload = course_payload(
            title="Python publié modifié",
            slug="python-publie",
            status="published",
        )
        response = client.post(edit_url, payload, HTTP_X_CSRFTOKEN=token)
        self.assertRedirects(
            response,
            reverse("users:course_manage_detail", args=[str(self.published["_id"])]),
            fetch_redirect_response=False,
        )
        updated = repository.get_course(self.published["_id"])
        self.assertEqual(updated["title"], "Python publié modifié")
        self.assertEqual(updated["slug"], "python-publie")

        admin_client = self._authenticate(Client(enforce_csrf_checks=True), self.admin)
        other_edit_url = reverse(
            "users:course_manage_edit", args=[str(self.other_published["_id"])]
        )
        admin_client.get(other_edit_url)
        admin_token = admin_client.cookies["csrftoken"].value
        response = admin_client.post(
            other_edit_url,
            course_payload(title="Design UX admin", slug="design-ux", status="published"),
            HTTP_X_CSRFTOKEN=admin_token,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            repository.get_course(self.other_published["_id"])["title"], "Design UX admin"
        )

    def test_delete_requires_confirmation_and_a_post_request(self):
        client = self._authenticate(Client(enforce_csrf_checks=True), self.owner)
        delete_url = reverse("users:course_manage_delete", args=[str(self.draft["_id"])])
        response = client.get(delete_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm deletion")
        self.assertIsNotNone(repository.get_course(self.draft["_id"]))
        token = client.cookies["csrftoken"].value
        response = client.post(delete_url, {}, HTTP_X_CSRFTOKEN=token)
        self.assertRedirects(response, reverse("users:course_manage_list"), fetch_redirect_response=False)
        self.assertIsNone(repository.get_course(self.draft["_id"]))

    def test_toggle_publishes_and_unpublishes_a_course(self):
        client = self._authenticate(Client(enforce_csrf_checks=True), self.owner)
        toggle_url = reverse("users:course_manage_publish", args=[str(self.draft["_id"])])
        token = self._csrf_token(client, reverse("users:course_manage_list"))
        detail_url = reverse("users:course_detail", args=[self.draft["slug"]])
        self.assertEqual(self.client.get(detail_url).status_code, 404)

        response = client.post(toggle_url, {}, HTTP_X_CSRFTOKEN=token)
        self.assertRedirects(
            response,
            reverse("users:course_manage_detail", args=[str(self.draft["_id"])]),
            fetch_redirect_response=False,
        )
        self.assertEqual(repository.get_course(self.draft["_id"])["status"], "published")
        self.assertEqual(self.client.get(detail_url).status_code, 200)

        response = client.post(toggle_url, {}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(repository.get_course(self.draft["_id"])["status"], "draft")
        self.assertEqual(self.client.get(detail_url).status_code, 404)

    def test_toggle_rejects_get_requests(self):
        client = self._authenticate(Client(), self.owner)
        self.assertEqual(
            client.get(reverse("users:course_manage_publish", args=[str(self.draft["_id"])])).status_code,
            405,
        )

    def test_posts_without_a_csrf_token_are_rejected(self):
        client = self._authenticate(Client(enforce_csrf_checks=True), self.owner)
        self.assertEqual(
            client.post(reverse("users:course_manage_create"), course_payload()).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                reverse("users:course_manage_delete", args=[str(self.draft["_id"])]), {}
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                reverse("users:course_manage_publish", args=[str(self.draft["_id"])]), {}
            ).status_code,
            403,
        )
        self.assertIsNotNone(repository.get_course(self.draft["_id"]))

    def test_storage_failures_render_friendly_messages(self):
        with patch("apps.courses.repository.list_courses", side_effect=PyMongoError):
            response = self.client.get(reverse("users:courses"))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "temporarily unavailable", status_code=503)

        with patch("apps.courses.repository.get_course_by_slug", side_effect=PyMongoError):
            response = self.client.get(
                reverse("users:course_detail", args=[self.published["slug"]])
            )
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "temporarily unavailable", status_code=503)

        client = self._authenticate(Client(), self.owner)
        with patch("apps.courses.repository.list_courses", side_effect=PyMongoError):
            response = client.get(reverse("users:course_manage_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "unavailable")

        with patch("apps.courses.repository.get_course", side_effect=PyMongoError):
            response = client.get(
                reverse("users:course_manage_edit", args=[str(self.draft["_id"])])
            )
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Service temporarily unavailable", status_code=503)

    def test_optional_session_user_storage_failures_fall_back_to_anonymous(self):
        session = self.client.session
        session["user_id"] = str(self.owner["_id"])
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        with patch("apps.courses.views.get_active_user", side_effect=UserStorageError):
            response = self.client.get(
                reverse("users:course_detail", args=[self.published["slug"]])
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Python publié")

    def test_student_dashboard_storage_failure_is_friendly(self):
        client = self._authenticate(Client(), self.owner)
        with patch("apps.courses.repository.list_courses", side_effect=PyMongoError):
            response = client.get(reverse("users:backoffice_courses"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "temporairement indisponibles")

    def test_update_and_toggle_storage_failures_are_handled(self):
        client = self._authenticate(Client(enforce_csrf_checks=True), self.owner)
        edit_url = reverse("users:course_manage_edit", args=[str(self.published["_id"])])
        client.get(edit_url)
        token = client.cookies["csrftoken"].value
        with patch("apps.courses.repository.update_course", side_effect=PyMongoError):
            response = client.post(
                edit_url,
                course_payload(title="Titre non enregistré", slug="python-publie"),
                HTTP_X_CSRFTOKEN=token,
            )
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "enregistrer les modifications", status_code=503)
        self.assertEqual(
            repository.get_course(self.published["_id"])["title"], "Python publié"
        )

        toggle_url = reverse("users:course_manage_publish", args=[str(self.draft["_id"])])
        with patch("apps.courses.repository.update_course", side_effect=PyMongoError):
            response = client.post(toggle_url, {}, HTTP_X_CSRFTOKEN=token)
        self.assertRedirects(response, reverse("users:course_manage_list"), fetch_redirect_response=False)
        followed = client.get(reverse("users:course_manage_list"))
        self.assertContains(followed, "Impossible de modifier la publication du cours.")
        self.assertEqual(repository.get_course(self.draft["_id"])["status"], "draft")

    def test_create_and_delete_storage_failures_are_handled(self):
        client = self._authenticate(Client(enforce_csrf_checks=True), self.owner)
        create_url = reverse("users:course_manage_create")
        client.get(create_url)
        token = client.cookies["csrftoken"].value
        with patch("apps.courses.repository.slug_exists", side_effect=PyMongoError):
            response = client.post(create_url, course_payload(), HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Impossible de préparer", status_code=503)

        delete_url = reverse("users:course_manage_delete", args=[str(self.draft["_id"])])
        delete_client = self._authenticate(Client(enforce_csrf_checks=True), self.owner)
        delete_client.get(delete_url)
        delete_token = delete_client.cookies["csrftoken"].value
        with patch("apps.courses.repository.delete_course", side_effect=PyMongoError):
            response = delete_client.post(delete_url, {}, HTTP_X_CSRFTOKEN=delete_token)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Impossible de supprimer", status_code=503)
        self.assertIsNotNone(repository.get_course(self.draft["_id"]))

    def test_xss_payload_in_title_and_description_is_auto_escaped(self):
        xss_course = course_document(
            slug="xss-test",
            title="<script>alert('xss-title')</script>",
            description="<img src=x onerror=alert('xss-desc')>",
            status="published",
            instructor_id=self.owner["_id"],
        )
        self.collection.insert_one(xss_course)
        # Public list
        response = self.client.get(reverse("users:courses"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<script>alert('xss-title')</script>")
        self.assertContains(response, "&lt;script&gt;alert(&#x27;xss-title&#x27;)&lt;/script&gt;")

        # Detail view
        detail_response = self.client.get(reverse("users:course_detail", args=["xss-test"]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertNotContains(detail_response, "<script>alert('xss-title')</script>")
        self.assertNotContains(detail_response, "<img src=x onerror=alert('xss-desc')>")
        self.assertContains(detail_response, "&lt;img src=x onerror=alert(&#x27;xss-desc&#x27;)&gt;")

    def test_nosql_injection_payload_in_search_and_filter_params(self):
        response = self.client.get(reverse("users:courses"), {"q": '{"$ne": ""}', "category": '{"$gt": ""}'})
        self.assertEqual(response.status_code, 200)

        client = self._authenticate(Client(), self.owner)
        manage_response = client.get(reverse("users:course_manage_list"), {"q": '{"$ne": ""}', "status": '{"$gt": ""}'})
        self.assertEqual(manage_response.status_code, 200)

    def test_pagination_out_of_range_and_invalid_values(self):
        for invalid_page in ("-1", "0", "99999", "abc"):
            with self.subTest(page=invalid_page):
                response = self.client.get(reverse("users:courses"), {"page": invalid_page})
                self.assertEqual(response.status_code, 200)

    def test_invalid_object_id_or_slug_returns_404_never_500(self):
        client = self._authenticate(Client(), self.owner)
        invalid_ids = ["not-an-id", "123", "507f1f77bcf86cd79943901", '{"$ne": 1}']
        for inv_id in invalid_ids:
            with self.subTest(inv_id=inv_id):
                for route in ("course_manage_detail", "course_manage_edit", "course_manage_delete"):
                    res = client.get(reverse(f"users:{route}", args=[inv_id]))
                    self.assertEqual(res.status_code, 404)

        invalid_slugs = ["non-existent-course", "slug-with-$operator", "$ne"]
        for inv_slug in invalid_slugs:
            with self.subTest(inv_slug=inv_slug):
                res = self.client.get(f"/users/cours/{inv_slug}/")
                self.assertEqual(res.status_code, 404)
