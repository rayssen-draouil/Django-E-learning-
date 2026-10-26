from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId
from django.test import SimpleTestCase
from pymongo.errors import DuplicateKeyError, PyMongoError

from apps.courses import services


def data(**overrides):
    values = {
        "title": "Python pour débutants",
        "slug": "",
        "description": "Apprenez les bases du langage Python.",
        "category": "programming",
        "level": "beginner",
        "is_free": True,
        "price": 0.0,
        "thumbnail": "",
        "status": "draft",
    }
    values.update(overrides)
    return values


def course(**overrides):
    now = datetime.now(timezone.utc)
    document = {
        "_id": ObjectId(),
        "title": "Cours existant",
        "slug": "cours-existant",
        "description": "Description du cours existant.",
        "category": "programming",
        "level": "beginner",
        "price": 0.0,
        "is_free": True,
        "thumbnail": "",
        "status": "draft",
        "instructor_id": ObjectId(),
        "instructor_name": "Ada Lovelace",
        "created_at": now,
        "updated_at": now,
    }
    document.update(overrides)
    return document


class CourseServiceTests(SimpleTestCase):
    def setUp(self):
        self.owner = {"_id": ObjectId(), "role": "instructor",
                      "first_name": "Ada", "last_name": "Lovelace"}
        self.other = {"_id": ObjectId(), "role": "instructor",
                      "first_name": "Grace", "last_name": "Hopper"}
        self.admin = {"_id": ObjectId(), "role": "admin",
                      "first_name": "Admin", "last_name": "System"}
        self.student = {"_id": ObjectId(), "role": "student"}

    def test_only_instructors_and_admins_can_create(self):
        with self.assertRaises(services.CoursePermissionError):
            services.create_course(data=data(), user=self.student)
        with self.assertRaises(services.CoursePermissionError):
            services.list_managed_courses(self.student)

    def test_create_generates_a_unique_slug_and_owner_fields(self):
        with patch("apps.courses.services.repository.slug_exists", side_effect=[True, False]) as slug_exists, \
             patch("apps.courses.services.repository.insert_course", return_value=ObjectId()) as insert:
            created = services.create_course(
                data=data(title="Python"), user=self.owner
            )
        self.assertEqual(created["slug"], "python-2")
        self.assertEqual(created["instructor_id"], self.owner["_id"])
        self.assertEqual(created["instructor_name"], "Ada Lovelace")
        self.assertEqual(created["status"], "draft")
        self.assertIsInstance(created["created_at"], datetime)
        self.assertEqual(slug_exists.call_count, 2)
        insert.assert_called_once_with(created)

    def test_create_keeps_an_explicit_slug(self):
        with patch("apps.courses.services.repository.slug_exists", return_value=False), \
             patch("apps.courses.services.repository.insert_course"):
            created = services.create_course(data=data(slug="mon-cours", price=25), user=self.owner)
        self.assertEqual(created["slug"], "mon-cours")
        self.assertEqual(created["price"], 25.0)

    def test_create_maps_storage_and_duplicate_errors(self):
        with patch("apps.courses.services.repository.slug_exists", return_value=False), \
             patch("apps.courses.services.repository.insert_course", side_effect=DuplicateKeyError("dup")):
            with self.assertRaises(services.CourseStorageError):
                services.create_course(data=data(), user=self.owner)

        with patch("apps.courses.services.repository.slug_exists", side_effect=PyMongoError):
            with self.assertRaises(services.CourseStorageError):
                services.create_course(data=data(), user=self.owner)

        with patch("apps.courses.services.repository.slug_exists", return_value=False), \
             patch("apps.courses.services.repository.insert_course", side_effect=PyMongoError):
            with self.assertRaises(services.CourseStorageError):
                services.create_course(data=data(), user=self.owner)

    def test_get_managed_course_validates_id_existence_and_ownership(self):
        document = course(instructor_id=self.owner["_id"])
        with self.assertRaises(services.CourseNotFoundError):
            services.get_managed_course("not-an-object-id", self.owner)
        with patch("apps.courses.services.repository.get_course", return_value=None):
            with self.assertRaises(services.CourseNotFoundError):
                services.get_managed_course(str(document["_id"]), self.owner)
        with patch("apps.courses.services.repository.get_course", return_value=document):
            with self.assertRaises(services.CoursePermissionError):
                services.get_managed_course(str(document["_id"]), self.other)
            self.assertEqual(
                services.get_managed_course(str(document["_id"]), self.admin), document
            )

    def test_get_managed_course_maps_storage_errors(self):
        with patch("apps.courses.services.repository.get_course", side_effect=PyMongoError):
            with self.assertRaises(services.CourseStorageError):
                services.get_managed_course(str(ObjectId()), self.owner)

    def test_update_enforces_ownership_and_updates_fields(self):
        document = course(instructor_id=self.owner["_id"])
        payload = data(title="Titre modifié", slug="", status="published", price=10)
        with patch("apps.courses.services.repository.get_course", return_value=document), \
             patch("apps.courses.services.repository.update_course", return_value=True) as update:
            with self.assertRaises(services.CoursePermissionError):
                services.update_managed_course(str(document["_id"]), data=payload, user=self.other)
            updated = services.update_managed_course(
                str(document["_id"]), data=payload, user=self.owner
            )
        self.assertEqual(updated["title"], "Titre modifié")
        self.assertEqual(updated["slug"], "cours-existant")
        self.assertEqual(updated["status"], "published")
        self.assertEqual(update.call_args.args[1]["price"], 10.0)

    def test_update_can_change_the_slug_and_maps_errors(self):
        document = course(instructor_id=self.owner["_id"])
        with patch("apps.courses.services.repository.get_course", return_value=document), \
             patch("apps.courses.services.repository.slug_exists", return_value=False), \
             patch("apps.courses.services.repository.update_course", return_value=True) as update:
            services.update_managed_course(
                str(document["_id"]), data=data(slug="nouveau-slug"), user=self.owner
            )
        self.assertEqual(update.call_args.args[1]["slug"], "nouveau-slug")

        with patch("apps.courses.services.repository.get_course", return_value=document), \
             patch("apps.courses.services.repository.slug_exists", return_value=False), \
             patch("apps.courses.services.repository.update_course", side_effect=PyMongoError):
            with self.assertRaises(services.CourseStorageError):
                services.update_managed_course(
                    str(document["_id"]), data=data(slug="autre"), user=self.owner
                )

        with patch("apps.courses.services.repository.get_course", return_value=document), \
             patch("apps.courses.services.repository.update_course", return_value=False):
            with self.assertRaises(services.CourseNotFoundError):
                services.update_managed_course(
                    str(document["_id"]), data=data(), user=self.owner
                )

    def test_toggle_publication_and_delete_enforce_ownership(self):
        draft = course(instructor_id=self.owner["_id"], status="draft")
        with patch("apps.courses.services.repository.get_course", return_value=draft), \
             patch("apps.courses.services.repository.update_course", return_value=True) as update:
            toggled = services.toggle_managed_course_status(str(draft["_id"]), user=self.owner)
        self.assertEqual(toggled["status"], "published")
        self.assertEqual(update.call_args.args[1]["status"], "published")

        published = course(instructor_id=self.owner["_id"], status="published")
        with patch("apps.courses.services.repository.get_course", return_value=published), \
             patch("apps.courses.services.repository.update_course", return_value=True):
            toggled = services.toggle_managed_course_status(str(published["_id"]), user=self.owner)
        self.assertEqual(toggled["status"], "draft")

        with patch("apps.courses.services.repository.get_course", return_value=published), \
             patch("apps.courses.services.repository.delete_course", return_value=True) as delete:
            with self.assertRaises(services.CoursePermissionError):
                services.delete_managed_course(str(published["_id"]), user=self.other)
            services.delete_managed_course(str(published["_id"]), user=self.owner)
        delete.assert_called_once_with(published["_id"])

    def test_toggle_and_delete_map_storage_errors(self):
        document = course(instructor_id=self.owner["_id"])
        with patch("apps.courses.services.repository.get_course", return_value=document), \
             patch("apps.courses.services.repository.update_course", side_effect=PyMongoError):
            with self.assertRaises(services.CourseStorageError):
                services.toggle_managed_course_status(str(document["_id"]), user=self.owner)
        with patch("apps.courses.services.repository.get_course", return_value=document), \
             patch("apps.courses.services.repository.delete_course", side_effect=PyMongoError):
            with self.assertRaises(services.CourseStorageError):
                services.delete_managed_course(str(document["_id"]), user=self.owner)

    def test_public_list_applies_filters_pagination_and_clamps_the_page(self):
        with patch("apps.courses.services.repository.count_courses", return_value=25) as count, \
             patch("apps.courses.services.repository.list_courses", return_value=[]) as listing:
            result = services.list_public_courses(
                search="python", category="programming", level="beginner", page=99
            )
        self.assertEqual(result["page"], 3)
        self.assertEqual(result["page_count"], 3)
        self.assertEqual(result["previous_page"], 2)
        self.assertIsNone(result["next_page"])
        self.assertEqual(result["total"], 25)
        count.assert_called_once_with(
            published_only=True, search="python", category="programming", level="beginner"
        )
        self.assertEqual(listing.call_args.kwargs["skip"], 18)
        self.assertEqual(listing.call_args.kwargs["limit"], services.PAGE_SIZE)
        self.assertTrue(listing.call_args.kwargs["published_only"])

    def test_public_list_exposes_the_four_sort_choices_and_defaults_invalid_values(self):
        self.assertEqual(
            [value for value, _label in services.PUBLIC_SORT_CHOICES],
            ["recent", "title", "price_asc", "duration_asc"],
        )
        with patch("apps.courses.services.repository.count_courses", return_value=0), \
             patch("apps.courses.services.repository.list_courses", return_value=[]) as listing:
            services.list_public_courses(sort_by="duration_asc")
            self.assertEqual(listing.call_args.kwargs["sort_by"], "duration_asc")
            services.list_public_courses(sort_by="price_desc")
            self.assertEqual(listing.call_args.kwargs["sort_by"], "recent")

    def test_public_list_maps_storage_errors(self):
        with patch("apps.courses.services.repository.count_courses", side_effect=PyMongoError):
            with self.assertRaises(services.CourseStorageError):
                services.list_public_courses()

    def test_managed_list_scope_depends_on_role(self):
        with patch("apps.courses.services.repository.list_courses", return_value=[]) as listing:
            services.list_managed_courses(self.owner)
            self.assertEqual(listing.call_args.kwargs["instructor_id"], self.owner["_id"])
            services.list_managed_courses(self.admin)
            self.assertIsNone(listing.call_args.kwargs["instructor_id"])
        with patch("apps.courses.services.repository.list_courses", side_effect=PyMongoError):
            with self.assertRaises(services.CourseStorageError):
                services.list_managed_courses(self.owner)

    def test_public_detail_hides_drafts_from_everyone_but_owner_and_admin(self):
        published = course(status="published")
        draft = course(status="draft", instructor_id=self.owner["_id"])
        with patch("apps.courses.services.repository.get_course_by_slug", return_value=published):
            self.assertEqual(services.get_course_detail("published", None), published)
        with patch("apps.courses.services.repository.get_course_by_slug", return_value=draft):
            self.assertEqual(services.get_course_detail("draft", self.owner), draft)
            self.assertEqual(services.get_course_detail("draft", self.admin), draft)
            for viewer in (None, self.student, self.other):
                with self.assertRaises(services.CourseNotFoundError):
                    services.get_course_detail("draft", viewer)
        with patch("apps.courses.services.repository.get_course_by_slug", return_value=None):
            with self.assertRaises(services.CourseNotFoundError):
                services.get_course_detail("unknown", None)
        with self.assertRaises(services.CourseNotFoundError):
            services.get_course_detail("", None)
        with self.assertRaises(services.CourseNotFoundError):
            services.get_course_detail("x" * 201, None)
        with patch("apps.courses.services.repository.get_course_by_slug", side_effect=PyMongoError):
            with self.assertRaises(services.CourseStorageError):
                services.get_course_detail("python", None)

    def test_can_manage_only_for_the_owner_or_an_admin(self):
        document = course(instructor_id=self.owner["_id"])
        self.assertTrue(services.can_manage(document, self.owner))
        self.assertTrue(services.can_manage(document, self.admin))
        self.assertFalse(services.can_manage(document, self.other))
        self.assertFalse(services.can_manage(document, None))

    def test_ensure_unique_slug_falls_back_to_a_default_and_appends_suffixes(self):
        with patch("apps.courses.services.repository.slug_exists", return_value=False) as slug_exists:
            self.assertEqual(services.ensure_unique_slug("!!!"), "cours")
            slug_exists.assert_called_once_with("cours", exclude_id=None)
        with patch("apps.courses.services.repository.slug_exists", side_effect=[True, True, False]):
            self.assertEqual(services.ensure_unique_slug("Cours Python"), "cours-python-3")

    def test_parse_object_id_rejects_invalid_values(self):
        with self.assertRaises(services.CourseNotFoundError):
            services.parse_object_id("invalid")
        with self.assertRaises(services.CourseNotFoundError):
            services.parse_object_id(None)
        value = ObjectId()
        self.assertEqual(services.parse_object_id(value), value)
        self.assertEqual(services.parse_object_id(str(value)), value)
