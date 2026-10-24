from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from bson import ObjectId
from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from config.mongodb import db
from . import repositories, services
from .forms import AvailabilitySlotForm


def future_date_str(days_ahead: int = 3) -> str:
    return (services.current_local_date() + timedelta(days=days_ahead)).isoformat()


def valid_slot_payload(**overrides) -> dict[str, str]:
    payload = {
        "date": future_date_str(3),
        "start_time": "10:00",
        "end_time": "11:00",
        "topic": "Python Programming",
        "custom_topic": "",
        "session_type": "online",
        "location": "",
    }
    payload.update(overrides)
    return payload


class AvailabilityFormValidationTests(SimpleTestCase):
    def test_valid_online_slot_computes_duration_and_normalizes_fields(self):
        form = AvailabilitySlotForm(valid_slot_payload())
        self.assertTrue(form.is_valid(), form.errors)
        doc = form.to_document()
        self.assertEqual(doc["duration_minutes"], 60)
        self.assertEqual(doc["topic"], "Python Programming")
        self.assertEqual(doc["custom_topic"], "")
        self.assertEqual(doc["session_type"], "online")
        self.assertEqual(doc["location"], "")

    def test_missing_required_fields_return_friendly_messages(self):
        form = AvailabilitySlotForm({})
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Choose a date so students know when you're available.",
            form.errors.get("date", []),
        )
        self.assertIn(
            "When does your availability begin?",
            form.errors.get("start_time", []),
        )
        self.assertIn(
            "Your end time must be later than your start time.",
            form.errors.get("end_time", []),
        )
        self.assertIn(
            "Choose a mentoring topic to help students find the right session.",
            form.errors.get("topic", []),
        )
        self.assertIn(
            "Please select whether this session is Online or In-person.",
            form.errors.get("session_type", []),
        )

    def test_rejects_past_dates(self):
        past = (services.current_local_date() - timedelta(days=1)).isoformat()
        form = AvailabilitySlotForm(valid_slot_payload(date=past))
        self.assertFalse(form.is_valid())
        self.assertIn(
            "That date has already passed. Please select today or a future date.",
            form.errors.get("date", []),
        )

    def test_rejects_end_time_earlier_or_equal_to_start_time(self):
        for end_val in ("10:00", "09:30"):
            with self.subTest(end_time=end_val):
                form = AvailabilitySlotForm(
                    valid_slot_payload(start_time="10:00", end_time=end_val)
                )
                self.assertFalse(form.is_valid())
                self.assertIn(
                    "Your end time must be later than your start time.",
                    form.errors.get("end_time", []),
                )

    def test_rejects_duration_shorter_than_minimum_or_longer_than_maximum(self):
        too_short = AvailabilitySlotForm(
            valid_slot_payload(start_time="10:00", end_time="10:10")
        )
        self.assertFalse(too_short.is_valid())
        self.assertIn(
            "Choose a session lasting at least 15 minutes.",
            too_short.errors.get("end_time", []),
        )

        too_long = AvailabilitySlotForm(
            valid_slot_payload(start_time="08:00", end_time="13:00")
        )
        self.assertFalse(too_long.is_valid())
        self.assertIn(
            "Sessions cannot exceed 4 hours (240 minutes).",
            too_long.errors.get("end_time", []),
        )

    def test_other_topic_requires_non_empty_bounded_custom_topic(self):
        missing_custom = AvailabilitySlotForm(
            valid_slot_payload(topic="Other", custom_topic="   ")
        )
        self.assertFalse(missing_custom.is_valid())
        self.assertIn(
            "Tell us which topic you want to mentor.",
            missing_custom.errors.get("custom_topic", []),
        )

        too_long_custom = AvailabilitySlotForm(
            valid_slot_payload(topic="Other", custom_topic="A" * 121)
        )
        self.assertFalse(too_long_custom.is_valid())
        self.assertIn(
            "Custom topic must be 120 characters or fewer.",
            too_long_custom.errors.get("custom_topic", []),
        )

        valid_custom = AvailabilitySlotForm(
            valid_slot_payload(topic="Other", custom_topic="Distributed Systems")
        )
        self.assertTrue(valid_custom.is_valid(), valid_custom.errors)
        self.assertEqual(valid_custom.to_document()["custom_topic"], "Distributed Systems")

    def test_in_person_keeps_location_while_online_clears_location(self):
        in_person = AvailabilitySlotForm(
            valid_slot_payload(session_type="in_person", location="Room B204")
        )
        self.assertTrue(in_person.is_valid(), in_person.errors)
        self.assertEqual(in_person.to_document()["location"], "Room B204")

        online = AvailabilitySlotForm(
            valid_slot_payload(session_type="online", location="Should be cleared")
        )
        self.assertTrue(online.is_valid(), online.errors)
        self.assertEqual(online.to_document()["location"], "")


class AvailabilityServiceTests(SimpleTestCase):
    def setUp(self):
        self.mentor = {"_id": ObjectId(), "role": "mentor"}
        self.other_mentor = {"_id": ObjectId(), "role": "mentor"}
        self.slot_id = ObjectId()
        self.slot_doc = {
            "_id": self.slot_id,
            "mentor_id": self.mentor["_id"],
            "date": future_date_str(3),
            "start_time": "10:00",
            "end_time": "11:00",
            "duration_minutes": 60,
            "topic": "Django",
            "custom_topic": "",
            "session_type": "online",
            "location": "",
            "status": "available",
            "is_booked": False,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

    def test_non_mentor_roles_are_rejected_by_service_layer(self):
        for role in ("student", "instructor", "admin"):
            with self.subTest(role=role):
                user = {"_id": ObjectId(), "role": role}
                with self.assertRaises(services.AvailabilityPermissionError):
                    services.list_mentor_slots(user)
                with self.assertRaises(services.AvailabilityPermissionError):
                    services.create_availability_slot(
                        data=valid_slot_payload(), user=user
                    )

    def test_server_calculates_duration_and_ignores_client_supplied_duration_or_mentor_id(self):
        forged_mentor_id = ObjectId()
        payload = {
            **valid_slot_payload(start_time="14:00", end_time="15:30"),
            "duration_minutes": 9999,
            "mentor_id": forged_mentor_id,
        }
        with patch(
            "apps.mentors.services.repositories.find_overlapping_slots",
            return_value=[],
        ), patch(
            "apps.mentors.services.repositories.insert_availability",
            side_effect=lambda doc: doc["_id"],
        ) as insert_mock:
            services.create_availability_slot(data=payload, user=self.mentor)

        saved_doc = insert_mock.call_args.args[0]
        self.assertEqual(saved_doc["mentor_id"], self.mentor["_id"])
        self.assertNotEqual(saved_doc["mentor_id"], forged_mentor_id)
        self.assertEqual(saved_doc["duration_minutes"], 90)

    def test_overlap_detection_rejects_conflicting_slot_and_allows_adjacent_slot(self):
        with patch(
            "apps.mentors.services.repositories.find_overlapping_slots",
            return_value=[self.slot_doc],
        ):
            with self.assertRaises(services.AvailabilityOverlapError) as ctx:
                services.create_availability_slot(
                    data=valid_slot_payload(start_time="10:30", end_time="11:30"),
                    user=self.mentor,
                )
            self.assertIn("overlaps", str(ctx.exception))

        # Adjacent slot (11:00-12:00 right after 10:00-11:00)
        with patch(
            "apps.mentors.services.repositories.find_overlapping_slots",
            return_value=[],
        ) as overlap_mock, patch(
            "apps.mentors.services.repositories.insert_availability",
            return_value=ObjectId(),
        ):
            services.create_availability_slot(
                data=valid_slot_payload(start_time="11:00", end_time="12:00"),
                user=self.mentor,
            )
            self.assertEqual(overlap_mock.call_args.kwargs["start_time"], "11:00")
            self.assertEqual(overlap_mock.call_args.kwargs["end_time"], "12:00")

    def test_editing_slot_excludes_current_slot_from_overlap_check(self):
        with patch(
            "apps.mentors.services.repositories.get_availability_slot",
            return_value=self.slot_doc,
        ), patch(
            "apps.mentors.services.repositories.find_overlapping_slots",
            return_value=[],
        ) as overlap_mock, patch(
            "apps.mentors.services.repositories.update_availability",
            return_value=True,
        ):
            services.update_availability_slot(
                str(self.slot_id),
                data=valid_slot_payload(start_time="10:00", end_time="11:00", topic="Django"),
                user=self.mentor,
            )
            self.assertEqual(
                overlap_mock.call_args.kwargs["exclude_slot_id"], self.slot_id
            )

    def test_mentor_cannot_access_edit_or_delete_another_mentors_slot(self):
        with patch(
            "apps.mentors.services.repositories.get_availability_slot",
            return_value=self.slot_doc,
        ):
            with self.assertRaises(services.AvailabilityPermissionError):
                services.get_managed_slot(str(self.slot_id), self.other_mentor)
            with self.assertRaises(services.AvailabilityPermissionError):
                services.update_availability_slot(
                    str(self.slot_id),
                    data=valid_slot_payload(),
                    user=self.other_mentor,
                )
            with self.assertRaises(services.AvailabilityPermissionError):
                services.delete_availability_slot(
                    str(self.slot_id), user=self.other_mentor
                )

    def test_booked_slot_cannot_be_deleted(self):
        booked_doc = dict(self.slot_doc, status="booked", is_booked=True)
        with patch(
            "apps.mentors.services.repositories.get_availability_slot",
            return_value=booked_doc,
        ):
            with self.assertRaises(services.AvailabilityBookedError):
                services.delete_availability_slot(str(self.slot_id), user=self.mentor)


class MongoAvailabilityIntegrationTests(SimpleTestCase):
    """End-to-end MongoDB persistence, index, overlap, and soft-delete tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.collection_name = f"test_mentor_availability_{uuid4().hex}"
        cls.test_collection = db[cls.collection_name]
        cls.original_collection = repositories.availability_collection
        repositories.availability_collection = cls.test_collection

    @classmethod
    def tearDownClass(cls):
        repositories.availability_collection = cls.original_collection
        db.drop_collection(cls.collection_name)
        super().tearDownClass()

    def setUp(self):
        self.test_collection.delete_many({})
        self.mentor_a = {"_id": ObjectId(), "role": "mentor"}
        self.mentor_b = {"_id": ObjectId(), "role": "mentor"}

    def test_indexes_and_crud_lifecycle_in_mongodb(self):
        repositories.create_indexes()
        index_info = self.test_collection.index_information()
        self.assertIn("mentor_availability_owner_schedule", index_info)
        self.assertIn("mentor_availability_owner_status_date", index_info)

        # 1. Create first slot 10:00 - 11:00
        slot_1_id = services.create_availability_slot(
            data=valid_slot_payload(
                date=future_date_str(5),
                start_time="10:00",
                end_time="11:00",
                topic="Python Programming",
                session_type="online",
            ),
            user=self.mentor_a,
        )
        self.assertIsInstance(slot_1_id, ObjectId)

        # 2. Adjacent slot 11:00 - 12:00 for same mentor succeeds
        slot_2_id = services.create_availability_slot(
            data=valid_slot_payload(
                date=future_date_str(5),
                start_time="11:00",
                end_time="12:00",
                topic="Django",
                session_type="in_person",
                location="Room 101",
            ),
            user=self.mentor_a,
        )
        self.assertIsInstance(slot_2_id, ObjectId)

        # 3. Overlapping slot 10:30 - 11:30 for same mentor is rejected
        with self.assertRaises(services.AvailabilityOverlapError):
            services.create_availability_slot(
                data=valid_slot_payload(
                    date=future_date_str(5),
                    start_time="10:30",
                    end_time="11:30",
                ),
                user=self.mentor_a,
            )

        # 4. Different mentor CAN create overlapping slot 10:00 - 11:00 on same date
        mentor_b_slot_id = services.create_availability_slot(
            data=valid_slot_payload(
                date=future_date_str(5),
                start_time="10:00",
                end_time="11:00",
            ),
            user=self.mentor_b,
        )
        self.assertIsInstance(mentor_b_slot_id, ObjectId)

        # 5. Verify Mentor A only reads their own 2 slots and accurate stats
        mentor_a_slots = services.list_mentor_slots(self.mentor_a)
        self.assertEqual(len(mentor_a_slots), 2)
        stats = services.get_mentor_availability_stats(self.mentor_a)
        self.assertEqual(stats["total_slots"], 2)
        self.assertEqual(stats["upcoming_slots"], 2)
        self.assertEqual(stats["online_slots"], 1)
        self.assertEqual(stats["in_person_slots"], 1)

        # 6. Edit slot_2 into overlap with slot_1 fails
        with self.assertRaises(services.AvailabilityOverlapError):
            services.update_availability_slot(
                str(slot_2_id),
                data=valid_slot_payload(
                    date=future_date_str(5),
                    start_time="10:30",
                    end_time="11:30",
                ),
                user=self.mentor_a,
            )

        # 7. Edit slot_2 to 11:30 - 12:30 succeeds
        updated = services.update_availability_slot(
            str(slot_2_id),
            data=valid_slot_payload(
                date=future_date_str(5),
                start_time="11:30",
                end_time="12:30",
                topic="Other",
                custom_topic="Microservices Architecture",
                session_type="in_person",
                location="Lab 4",
            ),
            user=self.mentor_a,
        )
        self.assertEqual(updated["start_time"], "11:30")
        self.assertEqual(updated["custom_topic"], "Microservices Architecture")

        # 8. Soft-delete slot_1 removes it from active list/stats and frees 10:00-11:00
        services.delete_availability_slot(str(slot_1_id), user=self.mentor_a)
        stats_after_delete = services.get_mentor_availability_stats(self.mentor_a)
        self.assertEqual(stats_after_delete["total_slots"], 1)

        raw_deleted = self.test_collection.find_one({"_id": slot_1_id})
        self.assertEqual(raw_deleted["status"], "inactive")
        self.assertFalse(raw_deleted["is_active"])

        # Recreating 10:00-11:00 now succeeds because soft-deleted slot is ignored
        new_slot_id = services.create_availability_slot(
            data=valid_slot_payload(
                date=future_date_str(5),
                start_time="10:00",
                end_time="11:00",
            ),
            user=self.mentor_a,
        )
        self.assertIsInstance(new_slot_id, ObjectId)


@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class AvailabilityViewTests(SimpleTestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.mentor = {
            "_id": ObjectId(),
            "first_name": "Amine",
            "last_name": "Benali",
            "email": "amine@skillforge.test",
            "role": "mentor",
            "is_active": True,
        }
        self.slot_id = ObjectId()
        self.slot_doc = {
            "_id": self.slot_id,
            "mentor_id": self.mentor["_id"],
            "date": future_date_str(4),
            "start_time": "09:00",
            "end_time": "10:00",
            "duration_minutes": 60,
            "topic": "Python Programming",
            "custom_topic": "",
            "session_type": "online",
            "location": "",
            "status": "available",
            "is_booked": False,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

    def authenticate_as(self, role: str = "mentor", user_id: ObjectId | None = None):
        uid = user_id or self.mentor["_id"]
        session = self.client.session
        session["user_id"] = str(uid)
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        user = dict(self.mentor, _id=uid, role=role)
        return patch("apps.users.decorators.get_active_user", return_value=user)

    def csrf_post(self, url: str, data: dict):
        get_resp = self.client.get(url)
        token = get_resp.cookies.get("csrftoken")
        if token is None:
            # Fetch CSRF cookie from create page if current URL doesn't set it
            get_resp = self.client.get(reverse("users:mentor_availability_create"))
            token = get_resp.cookies.get("csrftoken")
        self.assertIsNotNone(token)
        return self.client.post(url, data, HTTP_X_CSRFTOKEN=token.value)

    def test_strict_route_protection_blocks_anonymous_and_non_mentor_roles(self):
        protected_urls = [
            reverse("users:mentor_availability_list"),
            reverse("users:mentor_availability_create"),
            reverse("users:mentor_availability_detail", args=[str(self.slot_id)]),
            reverse("users:mentor_availability_edit", args=[str(self.slot_id)]),
            reverse("users:mentor_availability_delete", args=[str(self.slot_id)]),
        ]
        # Anonymous users are redirected to login
        for url in protected_urls:
            with self.subTest(anonymous_url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/users/login/", response["Location"])

        # Students, instructors, and admins receive 403 Forbidden
        for disallowed_role in ("student", "instructor", "admin"):
            with self.authenticate_as(role=disallowed_role):
                for url in protected_urls:
                    with self.subTest(role=disallowed_role, url=url):
                        self.assertEqual(self.client.get(url).status_code, 403)
                        self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_mentor_dashboard_renders_stats_slots_and_empty_state(self):
        url = reverse("users:mentor_availability_list")
        with self.authenticate_as("mentor"), patch(
            "apps.mentors.views.get_mentor_availability_stats",
            return_value={
                "total_slots": 0,
                "upcoming_slots": 0,
                "online_slots": 0,
                "in_person_slots": 0,
                "booked_slots": 0,
            },
        ), patch("apps.mentors.views.list_mentor_slots", return_value=[]):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "My Availability")
            self.assertContains(
                response, "Create and manage your coaching time slots."
            )
            self.assertContains(
                response,
                "No availability scheduled yet. Create your first time slot and make it easier for students to book a coaching session.",
            )
            self.assertContains(response, "Create your first slot")

    def test_reset_filters_displays_feedback_message(self):
        url = f"{reverse('users:mentor_availability_list')}?reset=1"
        with self.authenticate_as("mentor"), patch(
            "apps.mentors.views.get_mentor_availability_stats",
            return_value={
                "total_slots": 1,
                "upcoming_slots": 1,
                "online_slots": 1,
                "in_person_slots": 0,
                "booked_slots": 0,
            },
        ), patch(
            "apps.mentors.views.list_mentor_slots",
            return_value=[services.format_slot_for_display(self.slot_doc)],
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(
                response,
                "All filters cleared. You&#x27;re viewing your complete schedule again.",
            )

    def test_create_slot_enforces_csrf_and_persists_with_success_message(self):
        url = reverse("users:mentor_availability_create")
        with self.authenticate_as("mentor"), patch(
            "apps.mentors.views.create_availability_slot",
            return_value=self.slot_id,
        ) as create_mock:
            # Post without CSRF token is rejected with 403
            raw_post = self.client.post(url, valid_slot_payload())
            self.assertEqual(raw_post.status_code, 403)

            # Post with valid CSRF token succeeds and redirects
            response = self.csrf_post(url, valid_slot_payload())
            self.assertRedirects(
                response,
                reverse("users:mentor_availability_list"),
                fetch_redirect_response=False,
            )
            create_mock.assert_called_once()

    def test_failed_submission_preserves_entered_values_and_highlights_invalid_fields(self):
        url = reverse("users:mentor_availability_create")
        bad_payload = valid_slot_payload(
            start_time="14:00",
            end_time="13:00",
            topic="Other",
            custom_topic="",
            session_type="in_person",
            location="Innovation Hub Room 3",
        )
        with self.authenticate_as("mentor"):
            response = self.csrf_post(url, bad_payload)
            self.assertEqual(response.status_code, 200)
            self.assertContains(
                response, "Your end time must be later than your start time."
            )
            self.assertContains(
                response, "Tell us which topic you want to mentor."
            )
            self.assertContains(response, 'value="Innovation Hub Room 3"')
            self.assertContains(response, 'aria-invalid="true"')

    def test_overlap_error_is_displayed_on_form_without_500(self):
        url = reverse("users:mentor_availability_create")
        with self.authenticate_as("mentor"), patch(
            "apps.mentors.views.create_availability_slot",
            side_effect=services.AvailabilityOverlapError(),
        ):
            response = self.csrf_post(url, valid_slot_payload())
            self.assertEqual(response.status_code, 200)
            self.assertContains(
                response,
                "This time overlaps with another availability slot. Try a different time.",
            )

    def test_cross_mentor_ownership_and_invalid_id_handling(self):
        other_mentor_slot = dict(self.slot_doc, mentor_id=ObjectId())
        with self.authenticate_as("mentor"):
            self.assertEqual(
                self.client.get(
                    reverse("users:mentor_availability_edit", args=["not-an-id"])
                ).status_code,
                404,
            )
            with patch(
                "apps.mentors.services.repositories.get_availability_slot",
                return_value=other_mentor_slot,
            ):
                for route in (
                    "users:mentor_availability_detail",
                    "users:mentor_availability_edit",
                    "users:mentor_availability_delete",
                ):
                    with self.subTest(route=route):
                        resp = self.client.get(
                            reverse(route, args=[str(other_mentor_slot["_id"])])
                        )
                        self.assertEqual(resp.status_code, 403)

    def test_edit_and_delete_confirmation_workflow_for_owner(self):
        edit_url = reverse("users:mentor_availability_edit", args=[str(self.slot_id)])
        delete_url = reverse(
            "users:mentor_availability_delete", args=[str(self.slot_id)]
        )
        with self.authenticate_as("mentor"), patch(
            "apps.mentors.services.repositories.get_availability_slot",
            return_value=self.slot_doc,
        ):
            edit_get = self.client.get(edit_url)
            self.assertEqual(edit_get.status_code, 200)
            self.assertContains(edit_get, "Save Changes")

            with patch(
                "apps.mentors.views.update_availability_slot",
                return_value=self.slot_doc,
            ) as update_mock:
                edit_post = self.csrf_post(
                    edit_url,
                    valid_slot_payload(start_time="10:00", end_time="11:30"),
                )
                self.assertRedirects(
                    edit_post,
                    reverse("users:mentor_availability_list"),
                    fetch_redirect_response=False,
                )
                update_mock.assert_called_once()

            delete_get = self.client.get(delete_url)
            self.assertEqual(delete_get.status_code, 200)
            self.assertContains(delete_get, "Keep Slot")
            self.assertContains(delete_get, "Delete Availability")

            with patch("apps.mentors.views.delete_availability_slot") as delete_mock:
                delete_post = self.csrf_post(delete_url, {})
                self.assertRedirects(
                    delete_post,
                    reverse("users:mentor_availability_list"),
                    fetch_redirect_response=False,
                )
                delete_mock.assert_called_once()



# ---------------------------------------------------------------------------
# Mentorship Booking, Lifecycle & Review Tests (Phases 3 - 12)
# ---------------------------------------------------------------------------

class MentorshipBookingEngineTests(SimpleTestCase):
    def setUp(self):
        self.mentor_id = ObjectId()
        self.student_id = ObjectId()
        self.slot_id = ObjectId()
        self.student_user = {
            "_id": self.student_id,
            "first_name": "Alice",
            "last_name": "Student",
            "email": "alice@example.com",
            "role": "student",
        }
        self.mentor_user = {
            "_id": self.mentor_id,
            "first_name": "Bob",
            "last_name": "Mentor",
            "email": "bob@example.com",
            "role": "mentor",
        }
        self.slot_doc = {
            "_id": self.slot_id,
            "mentor_id": self.mentor_id,
            "date": future_date_str(5),
            "start_time": "14:00",
            "end_time": "15:00",
            "duration_minutes": 60,
            "topic": "Python Programming",
            "custom_topic": "",
            "session_type": "online",
            "location": "",
            "status": "available",
            "is_booked": False,
            "is_active": True,
        }

    def test_student_cannot_book_own_session(self):
        from .bookings import BookingValidationError, create_booking_request
        with self.assertRaises(BookingValidationError):
            create_booking_request(
                student_user=self.mentor_user,
                mentor_id=self.mentor_id,
                availability_id=self.slot_id,
                learning_objective="Learn Django architecture",
                problem_description="Need guidance with services pattern",
            )

    def test_cannot_book_inactive_or_mismatched_slot(self):
        from .bookings import BookingValidationError, create_booking_request
        with patch("apps.mentors.bookings.repositories.get_availability_slot", return_value=None):
            with self.assertRaises(BookingValidationError):
                create_booking_request(
                    student_user=self.student_user,
                    mentor_id=self.mentor_id,
                    availability_id=self.slot_id,
                    learning_objective="Learn Django architecture",
                    problem_description="Need guidance with services pattern",
                )

        different_mentor_slot = dict(self.slot_doc, mentor_id=ObjectId())
        with patch("apps.mentors.bookings.repositories.get_availability_slot", return_value=different_mentor_slot):
            with self.assertRaises(BookingValidationError):
                create_booking_request(
                    student_user=self.student_user,
                    mentor_id=self.mentor_id,
                    availability_id=self.slot_id,
                    learning_objective="Learn Django architecture",
                    problem_description="Need guidance with services pattern",
                )

    def test_create_booking_creates_pending_request_and_triggers_notifications(self):
        from .bookings import STATUS_PENDING, create_booking_request
        with patch("apps.mentors.bookings.repositories.get_availability_slot", return_value=self.slot_doc), \
             patch("apps.mentors.bookings.bookings_collection.find_one", return_value=None), \
             patch("apps.mentors.bookings.bookings_collection.insert_one") as mock_insert, \
             patch("apps.mentors.bookings.notifications.create_notification") as mock_notif, \
             patch("apps.mentors.bookings.email_service.send_templated_email") as mock_email:
            
            booking_id = create_booking_request(
                student_user=self.student_user,
                mentor_id=self.mentor_id,
                availability_id=self.slot_id,
                learning_objective="Master asynchronous Python and WebSockets",
                problem_description="Need help designing async worker tasks in Python.",
            )
            self.assertIsInstance(booking_id, ObjectId)
            mock_insert.assert_called_once()
            inserted_doc = mock_insert.call_args[0][0]
            self.assertEqual(inserted_doc["status"], STATUS_PENDING)
            self.assertEqual(inserted_doc["student_id"], self.student_id)
            self.assertEqual(inserted_doc["mentor_id"], self.mentor_id)

            mock_notif.assert_called_once()
            self.assertEqual(mock_notif.call_args[1]["recipient_id"], self.mentor_id)

    def test_mentor_accept_booking_atomically_claims_slot_and_notifies_student(self):
        from .bookings import STATUS_ACCEPTED, STATUS_PENDING, mentor_accept_booking
        booking_id = ObjectId()
        pending_booking = {
            "_id": booking_id,
            "student_id": self.student_id,
            "mentor_id": self.mentor_id,
            "availability_id": self.slot_id,
            "status": STATUS_PENDING,
            "date": self.slot_doc["date"],
            "start_time": self.slot_doc["start_time"],
            "end_time": self.slot_doc["end_time"],
            "topic": "Python Programming",
            "session_type": "online",
        }

        mock_update_result = MagicMock(matched_count=1)
        with patch("apps.mentors.bookings.bookings_collection.find_one", return_value=pending_booking), \
             patch("apps.mentors.repositories.availability_collection.update_one", return_value=mock_update_result) as mock_slot_update, \
             patch("apps.mentors.bookings.bookings_collection.update_one") as mock_book_update, \
             patch("apps.mentors.bookings.notifications.create_notification") as mock_notif, \
             patch("apps.mentors.bookings.email_service.send_templated_email") as mock_email:

            success = mentor_accept_booking(
                booking_id=booking_id,
                mentor_user=self.mentor_user,
                meeting_link="https://meet.google.com/abc-defg-hij",
            )
            self.assertTrue(success)
            mock_slot_update.assert_called_once()
            mock_book_update.assert_called_once()
            self.assertEqual(
                mock_book_update.call_args[0][1]["$set"]["status"],
                STATUS_ACCEPTED,
            )
            mock_notif.assert_called_once()
            self.assertEqual(mock_notif.call_args[1]["recipient_id"], self.student_id)

    def test_mentor_reject_booking_updates_status_and_notifies_student(self):
        from .bookings import STATUS_PENDING, STATUS_REJECTED, mentor_reject_booking
        booking_id = ObjectId()
        pending_booking = {
            "_id": booking_id,
            "student_id": self.student_id,
            "mentor_id": self.mentor_id,
            "availability_id": self.slot_id,
            "status": STATUS_PENDING,
            "date": self.slot_doc["date"],
            "start_time": self.slot_doc["start_time"],
        }
        with patch("apps.mentors.bookings.bookings_collection.find_one", return_value=pending_booking), \
             patch("apps.mentors.bookings.bookings_collection.update_one") as mock_book_update, \
             patch("apps.mentors.repositories.availability_collection.update_one"), \
             patch("apps.mentors.bookings.notifications.create_notification") as mock_notif, \
             patch("apps.mentors.bookings.email_service.send_templated_email"):

            success = mentor_reject_booking(
                booking_id=booking_id,
                mentor_user=self.mentor_user,
                rejection_reason="Schedule conflict with team standup.",
            )
            self.assertTrue(success)
            self.assertEqual(
                mock_book_update.call_args[0][1]["$set"]["status"],
                STATUS_REJECTED,
            )
            mock_notif.assert_called_once()
            self.assertEqual(mock_notif.call_args[1]["recipient_id"], self.student_id)

    def test_student_cancel_booking_releases_slot(self):
        from .bookings import STATUS_ACCEPTED, STATUS_CANCELLED, student_cancel_booking
        booking_id = ObjectId()
        accepted_booking = {
            "_id": booking_id,
            "student_id": self.student_id,
            "mentor_id": self.mentor_id,
            "availability_id": self.slot_id,
            "status": STATUS_ACCEPTED,
            "date": self.slot_doc["date"],
            "start_time": self.slot_doc["start_time"],
        }
        with patch("apps.mentors.bookings.bookings_collection.find_one", return_value=accepted_booking), \
             patch("apps.mentors.bookings.bookings_collection.update_one") as mock_book_update, \
             patch("apps.mentors.repositories.availability_collection.update_one") as mock_slot_release, \
             patch("apps.mentors.bookings.notifications.create_notification") as mock_notif, \
             patch("apps.mentors.bookings.email_service.send_templated_email"):

            success = student_cancel_booking(
                booking_id=booking_id,
                student_user=self.student_user,
                cancellation_reason="Personal emergency.",
            )
            self.assertTrue(success)
            self.assertEqual(
                mock_book_update.call_args[0][1]["$set"]["status"],
                STATUS_CANCELLED,
            )
            mock_slot_release.assert_called_once()
            self.assertEqual(
                mock_slot_release.call_args[0][1]["$set"]["status"],
                "available",
            )


class VerifiedReviewsTests(SimpleTestCase):
    def setUp(self):
        self.mentor_id = ObjectId()
        self.student_id = ObjectId()
        self.booking_id = ObjectId()
        self.student_user = {
            "_id": self.student_id,
            "first_name": "Elena",
            "last_name": "Rostova",
            "role": "student",
        }
        self.completed_booking = {
            "_id": self.booking_id,
            "student_id": self.student_id,
            "mentor_id": self.mentor_id,
            "status": "completed",
        }

    def test_review_requires_completed_session(self):
        from .reviews import ReviewValidationError, create_review
        active_booking = dict(self.completed_booking, status="accepted")
        with self.assertRaises(ReviewValidationError):
            create_review(
                booking=active_booking,
                student_user=self.student_user,
                rating=5,
                review_text="This was an outstanding coaching session!",
            )

    def test_review_requires_rating_between_1_and_5(self):
        from .reviews import ReviewValidationError, create_review
        for invalid_rating in (0, 6, "invalid"):
            with self.subTest(rating=invalid_rating):
                with self.assertRaises(ReviewValidationError):
                    create_review(
                        booking=self.completed_booking,
                        student_user=self.student_user,
                        rating=invalid_rating,
                        review_text="Detailed feedback about the mentorship experience.",
                    )

    def test_review_requires_minimum_10_characters(self):
        from .reviews import ReviewValidationError, create_review
        with self.assertRaises(ReviewValidationError):
            create_review(
                booking=self.completed_booking,
                student_user=self.student_user,
                rating=5,
                review_text="Too short",
            )

    def test_successful_review_creation_and_rating_summary_calculation(self):
        from .reviews import create_review, get_mentor_rating_summary
        with patch("apps.mentors.reviews.reviews_collection.insert_one") as mock_insert:
            review_id = create_review(
                booking=self.completed_booking,
                student_user=self.student_user,
                rating=5,
                review_text="Elena provided clear architecture diagrams and actionable code feedback.",
            )
            self.assertIsInstance(review_id, ObjectId)
            mock_insert.assert_called_once()

        mock_db_reviews = [{"rating": 5}, {"rating": 4}, {"rating": 5}]
        with patch("apps.mentors.reviews.reviews_collection.find", return_value=mock_db_reviews):
            summary = get_mentor_rating_summary(self.mentor_id)
            self.assertEqual(summary["review_count"], 3)
            self.assertEqual(summary["average_rating"], 4.7)
            self.assertTrue(summary["has_reviews"])


class NotificationReadTests(SimpleTestCase):
    def test_mark_notification_as_read_accepts_recipient_id_and_user_id(self):
        from . import notifications
        nid = ObjectId()
        uid = ObjectId()

        with patch("apps.mentors.notifications.notifications_collection.update_one") as mock_update:
            mock_update.return_value.matched_count = 1
            # Test with recipient_id
            res1 = notifications.mark_notification_as_read(notification_id=nid, recipient_id=uid)
            self.assertTrue(res1)
            self.assertEqual(mock_update.call_args[0][0], {"_id": nid, "recipient_id": uid})

            # Test with user_id
            res2 = notifications.mark_notification_as_read(notification_id=nid, user_id=uid)
            self.assertTrue(res2)
            self.assertEqual(mock_update.call_args[0][0], {"_id": nid, "recipient_id": uid})

    def test_mark_all_notifications_as_read_accepts_recipient_id(self):
        from . import notifications
        uid = ObjectId()
        with patch("apps.mentors.notifications.notifications_collection.update_many") as mock_update:
            mock_update.return_value.modified_count = 3
            count = notifications.mark_all_notifications_as_read(recipient_id=uid)
            self.assertEqual(count, 3)
            self.assertEqual(mock_update.call_args[0][0], {"recipient_id": uid, "is_read": False})


class RescheduleProposalTests(SimpleTestCase):
    def test_reschedule_form_validates_future_date_and_times(self):
        from .forms import RescheduleProposalForm
        from .services import current_local_date
        from datetime import timedelta

        tomorrow = (current_local_date() + timedelta(days=1)).isoformat()
        form = RescheduleProposalForm(
            data={
                "proposed_date": tomorrow,
                "proposed_start_time": "14:00",
                "proposed_end_time": "15:00",
                "reschedule_note": "Let's meet tomorrow instead.",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_reschedule_form_rejects_past_date(self):
        from .forms import RescheduleProposalForm
        from .services import current_local_date
        from datetime import timedelta

        yesterday = (current_local_date() - timedelta(days=1)).isoformat()
        form = RescheduleProposalForm(
            data={
                "proposed_date": yesterday,
                "proposed_start_time": "10:00",
                "proposed_end_time": "11:00",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("proposed_date", form.errors)
        self.assertIn("cannot be in the past", str(form.errors["proposed_date"]))

    def test_reschedule_form_rejects_end_time_before_start_time(self):
        from .forms import RescheduleProposalForm
        from .services import current_local_date
        from datetime import timedelta

        tomorrow = (current_local_date() + timedelta(days=1)).isoformat()
        form = RescheduleProposalForm(
            data={
                "proposed_date": tomorrow,
                "proposed_start_time": "15:00",
                "proposed_end_time": "14:00",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("proposed_end_time", form.errors)
        self.assertIn("later than proposed start time", str(form.errors["proposed_end_time"]))

    def test_reschedule_service_rejects_past_date(self):
        from .bookings import BookingValidationError, mentor_propose_reschedule
        from .services import current_local_date
        from datetime import timedelta

        yesterday = (current_local_date() - timedelta(days=1)).isoformat()
        mentor_id = ObjectId()
        booking_id = ObjectId()

        with patch("apps.mentors.bookings.bookings_collection.find_one", return_value={"_id": booking_id, "mentor_id": mentor_id, "status": "pending"}):
            with self.assertRaises(BookingValidationError) as ctx:
                mentor_propose_reschedule(
                    booking_id=booking_id,
                    mentor_user={"_id": mentor_id, "role": "mentor"},
                    proposed_date=yesterday,
                    proposed_start_time="10:00",
                    proposed_end_time="11:00",
                )
            self.assertIn("cannot be in the past", str(ctx.exception))

    def test_reschedule_service_allows_rescheduled_pending_status(self):
        from .bookings import STATUS_RESCHEDULED_PENDING, mentor_propose_reschedule
        from .services import current_local_date
        from datetime import timedelta

        tomorrow = (current_local_date() + timedelta(days=1)).isoformat()
        mentor_id = ObjectId()
        booking_id = ObjectId()

        booking_doc = {
            "_id": booking_id,
            "mentor_id": mentor_id,
            "student_id": ObjectId(),
            "status": STATUS_RESCHEDULED_PENDING,
            "date": tomorrow,
        }

        with patch("apps.mentors.bookings.bookings_collection.find_one", return_value=booking_doc), \
             patch("apps.mentors.bookings.bookings_collection.update_one") as mock_update, \
             patch("apps.mentors.notifications.create_notification"):
            res = mentor_propose_reschedule(
                booking_id=booking_id,
                mentor_user={"_id": mentor_id, "role": "mentor", "first_name": "Test", "last_name": "Mentor"},
                proposed_date=tomorrow,
                proposed_start_time="14:00",
                proposed_end_time="15:00",
            )
            self.assertTrue(res)
            mock_update.assert_called_once()

    def test_reschedule_service_rejects_date_before_original_session_date(self):
        from .bookings import BookingValidationError, mentor_propose_reschedule
        from .services import current_local_date
        from datetime import timedelta

        orig_date = (current_local_date() + timedelta(days=10)).isoformat()
        earlier_date = (current_local_date() + timedelta(days=5)).isoformat()
        mentor_id = ObjectId()
        booking_id = ObjectId()

        booking_doc = {
            "_id": booking_id,
            "mentor_id": mentor_id,
            "status": "pending",
            "date": orig_date,
        }

        with patch("apps.mentors.bookings.bookings_collection.find_one", return_value=booking_doc):
            with self.assertRaises(BookingValidationError) as ctx:
                mentor_propose_reschedule(
                    booking_id=booking_id,
                    mentor_user={"_id": mentor_id, "role": "mentor"},
                    proposed_date=earlier_date,
                    proposed_start_time="10:00",
                    proposed_end_time="11:00",
                )
            self.assertIn("cannot be before the original scheduled date", str(ctx.exception))

    def test_reschedule_form_rejects_date_before_min_date(self):
        from .forms import RescheduleProposalForm
        from .services import current_local_date
        from datetime import timedelta

        orig_date = (current_local_date() + timedelta(days=10)).isoformat()
        earlier_date = (current_local_date() + timedelta(days=5)).isoformat()

        form = RescheduleProposalForm(
            data={
                "proposed_date": earlier_date,
                "proposed_start_time": "10:00",
                "proposed_end_time": "11:00",
            },
            min_date=orig_date,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("proposed_date", form.errors)
        self.assertIn("cannot be before the original scheduled date", str(form.errors["proposed_date"]))



