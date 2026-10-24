from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

from bson import ObjectId
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.courses import repository

from .base import MongoTestCase, course_document, user_document


def course_payload(**overrides):
    data = {
        "title": "Cours de permission",
        "slug": "",
        "description": "Une description suffisamment détaillée pour la validation.",
        "category": "web",
        "level": "intermediate",
        "is_free": "on",
        "price": "",
        "thumbnail": "",
        "status": "draft",
    }
    data.update(overrides)
    return data


class CoursePermissionMatrixTests(MongoTestCase):
    """Explicit role/ownership matrix for every course action."""

    def setUp(self):
        super().setUp()
        self.owner = user_document(
            role="instructor", email="owner@example.com",
            first_name="Ada", last_name="Lovelace",
        )
        self.other = user_document(
            role="instructor", email="other@example.com",
            first_name="Grace", last_name="Hopper",
        )
        self.admin = user_document(role="admin", email="admin@example.com")
        self.student = user_document(role="student", email="student@example.com")
        self.published = course_document(
            slug="cours-publie",
            title="Cours publié",
            status="published",
            instructor_id=self.owner["_id"],
            instructor_name="Ada Lovelace",
        )
        self.draft = course_document(
            slug="cours-brouillon",
            title="Cours brouillon",
            status="draft",
            instructor_id=self.owner["_id"],
            instructor_name="Ada Lovelace",
        )
        self.collection.insert_many([self.published, self.draft])

    def _authenticate(self, user, *, enforce_csrf=False):
        client = Client(enforce_csrf_checks=enforce_csrf)
        session = client.session
        session["user_id"] = str(user["_id"])
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("apps.users.decorators.get_active_user", return_value=user))
        stack.enter_context(patch("apps.courses.views.get_active_user", return_value=user))
        return client

    def _protected_get_urls(self):
        return (
            reverse("courses:course_manage_list"),
            reverse("courses:course_manage_create"),
            reverse("courses:course_manage_detail", args=[str(self.draft["_id"])]),
            reverse("courses:course_manage_edit", args=[str(self.draft["_id"])]),
            reverse("courses:course_manage_delete", args=[str(self.draft["_id"])]),
        )

    def test_anonymous_can_read_published_courses_only(self):
        catalogue = self.client.get(reverse("courses:courses"))
        self.assertEqual(catalogue.status_code, 200)
        self.assertContains(catalogue, "Cours publié")
        self.assertNotContains(catalogue, "Cours brouillon")

        self.assertEqual(
            self.client.get(reverse("courses:course_detail", args=[self.published["slug"]])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("courses:course_detail", args=[self.draft["slug"]])).status_code,
            404,
        )

    def test_anonymous_cannot_reach_any_management_action(self):
        for url in self._protected_get_urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("users:login")))

        publish_url = reverse("courses:course_manage_publish", args=[str(self.draft["_id"])])
        self.assertEqual(self.client.post(publish_url).status_code, 302)
        self.assertEqual(repository.get_course(self.draft["_id"])["status"], "draft")

        create_url = reverse("courses:course_manage_create")
        self.assertEqual(self.client.post(create_url, course_payload()).status_code, 302)
        self.assertEqual(self.collection.count_documents({}), 2)

    def test_student_is_read_only(self):
        client = self._authenticate(self.student)
        self.assertEqual(client.get(reverse("courses:courses")).status_code, 200)
        self.assertEqual(
            client.get(reverse("courses:course_detail", args=[self.published["slug"]])).status_code,
            200,
        )
        self.assertEqual(
            client.get(reverse("courses:course_detail", args=[self.draft["slug"]])).status_code,
            404,
        )

        for url in self._protected_get_urls():
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 403)

        self.assertEqual(
            client.post(reverse("courses:course_manage_create"), course_payload()).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                reverse("courses:course_manage_delete", args=[str(self.draft["_id"])])
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                reverse("courses:course_manage_publish", args=[str(self.draft["_id"])])
            ).status_code,
            403,
        )
        self.assertEqual(self.collection.count_documents({}), 2)
        self.assertEqual(repository.get_course(self.draft["_id"])["status"], "draft")

    def test_owner_instructor_has_full_crud_on_own_courses(self):
        client = self._authenticate(self.owner)
        self.assertEqual(client.get(reverse("courses:course_manage_list")).status_code, 200)
        self.assertEqual(
            client.get(reverse("courses:course_manage_detail", args=[str(self.draft["_id"])])).status_code,
            200,
        )
        self.assertEqual(client.get(reverse("courses:course_manage_create")).status_code, 200)

        response = client.post(
            reverse("courses:course_manage_create"),
            course_payload(title="Cours créé par le propriétaire"),
        )
        self.assertEqual(response.status_code, 302)
        created = repository.get_course_by_slug("cours-cree-par-le-proprietaire")
        self.assertIsNotNone(created)
        self.assertEqual(created["instructor_id"], self.owner["_id"])

        edit_url = reverse("courses:course_manage_edit", args=[str(self.published["_id"])])
        self.assertEqual(client.get(edit_url).status_code, 200)
        response = client.post(
            edit_url,
            course_payload(title="Titre modifié", slug="cours-publie", status="published"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(repository.get_course(self.published["_id"])["title"], "Titre modifié")

        publish_url = reverse("courses:course_manage_publish", args=[str(self.draft["_id"])])
        self.assertEqual(client.post(publish_url).status_code, 302)
        self.assertEqual(repository.get_course(self.draft["_id"])["status"], "published")

        delete_url = reverse("courses:course_manage_delete", args=[str(self.draft["_id"])])
        self.assertEqual(client.get(delete_url).status_code, 200)
        self.assertIsNotNone(repository.get_course(self.draft["_id"]))
        self.assertEqual(client.post(delete_url).status_code, 302)
        self.assertIsNone(repository.get_course(self.draft["_id"]))

    def test_non_owner_instructor_is_denied_every_write_and_private_read(self):
        client = self._authenticate(self.other)
        for name in ("course_manage_detail", "course_manage_edit", "course_manage_delete"):
            with self.subTest(view=name):
                url = reverse(f"courses:{name}", args=[str(self.draft["_id"])])
                self.assertEqual(client.get(url).status_code, 403)

        with self.subTest(view="course_manage_edit"):
            self.assertEqual(
                client.post(
                    reverse("courses:course_manage_edit", args=[str(self.draft["_id"])]),
                    course_payload(title="Vol de cours"),
                ).status_code,
                403,
            )
        with self.subTest(view="course_manage_publish"):
            self.assertEqual(
                client.post(
                    reverse("courses:course_manage_publish", args=[str(self.draft["_id"])])
                ).status_code,
                403,
            )
        with self.subTest(view="course_manage_delete"):
            self.assertEqual(
                client.post(
                    reverse("courses:course_manage_delete", args=[str(self.draft["_id"])])
                ).status_code,
                403,
            )

        self.assertEqual(repository.get_course(self.draft["_id"])["status"], "draft")
        self.assertEqual(repository.get_course(self.draft["_id"])["title"], "Cours brouillon")
        self.assertEqual(
            client.get(reverse("courses:course_detail", args=[self.draft["slug"]])).status_code,
            404,
        )

    def test_admin_can_manage_every_course_and_draft(self):
        client = self._authenticate(self.admin)
        listing = client.get(reverse("courses:course_manage_list"))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "Cours publié")
        self.assertContains(listing, "Cours brouillon")

        for name in ("course_manage_detail", "course_manage_edit", "course_manage_delete"):
            with self.subTest(view=name):
                self.assertEqual(
                    client.get(reverse(f"courses:{name}", args=[str(self.draft["_id"])])).status_code,
                    200,
                )
        self.assertEqual(
            client.get(reverse("courses:course_detail", args=[self.draft["slug"]])).status_code,
            200,
        )

        edit_url = reverse("courses:course_manage_edit", args=[str(self.published["_id"])])
        response = client.post(
            edit_url,
            course_payload(title="Modifié par admin", slug="cours-publie", status="published"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(repository.get_course(self.published["_id"])["title"], "Modifié par admin")

        response = client.post(
            reverse("courses:course_manage_publish", args=[str(self.draft["_id"])])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(repository.get_course(self.draft["_id"])["status"], "published")

        response = client.post(
            reverse("courses:course_manage_delete", args=[str(self.draft["_id"])])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(repository.get_course(self.draft["_id"]))

        response = client.post(
            reverse("courses:course_manage_create"),
            course_payload(title="Cours administrateur"),
        )
        self.assertEqual(response.status_code, 302)
        created = repository.get_course_by_slug("cours-administrateur")
        self.assertEqual(created["instructor_id"], self.admin["_id"])

    def test_invalid_ids_missing_courses_and_methods_return_the_right_status(self):
        client = self._authenticate(self.owner)
        for name in ("course_manage_detail", "course_manage_edit", "course_manage_delete"):
            with self.subTest(view=name, value="invalid"):
                self.assertEqual(
                    client.get(reverse(f"courses:{name}", args=["not-an-object-id"])).status_code,
                    404,
                )
            with self.subTest(view=name, value="missing"):
                self.assertEqual(
                    client.get(reverse(f"courses:{name}", args=[str(ObjectId())])).status_code,
                    404,
                )

        publish_url = reverse("courses:course_manage_publish", args=[str(self.draft["_id"])])
        self.assertEqual(client.get(publish_url).status_code, 405)
        self.assertEqual(
            client.post(reverse("courses:course_manage_detail", args=[str(self.draft["_id"])])).status_code,
            405,
        )
        self.assertEqual(repository.get_course(self.draft["_id"])["status"], "draft")

    def test_state_changing_posts_require_a_csrf_token(self):
        client = self._authenticate(self.owner, enforce_csrf=True)
        self.assertEqual(
            client.post(
                reverse("courses:course_manage_create"),
                course_payload(title="Sans jeton CSRF"),
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                reverse("courses:course_manage_delete", args=[str(self.draft["_id"])])
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                reverse("courses:course_manage_publish", args=[str(self.draft["_id"])])
            ).status_code,
            403,
        )
        self.assertEqual(self.collection.count_documents({}), 2)
        self.assertEqual(repository.get_course(self.draft["_id"])["status"], "draft")

    def test_legacy_users_namespace_keeps_working_alongside_course_routes(self):
        self.assertEqual(reverse("users:courses"), "/users/cours/")
        self.assertEqual(reverse("courses:courses"), "/cours/")
        self.assertEqual(reverse("users:course_manage_list"), "/users/backoffice/cours/manage/")
        self.assertEqual(reverse("courses:course_manage_list"), "/backoffice/cours/manage/")

        response = self.client.get(reverse("users:courses"))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("courses:courses"))
        self.assertEqual(response.status_code, 200)
