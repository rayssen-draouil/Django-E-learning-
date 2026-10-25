from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId
from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from apps.support import services


def ticket_document(**overrides):
    ticket = {
        "_id": ObjectId(),
        "student_id": ObjectId(),
        "student_name": "Ada Lovelace",
        "student_email": "ada@example.com",
        "title": "Course video won't load",
        "description": "Video remains stuck.",
        "category": "courses",
        "priority": "normal",
        "status": "open",
        "assignee_id": None,
        "assignee_name": "",
        "created_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        "history": [],
        "resolved_at": None,
    }
    ticket.update(overrides)
    return ticket


@override_settings(SESSION_ENGINE="apps.users.tests.session_backend")
class SupportViewTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        self.student = {"_id": ObjectId(), "email": "ada@example.com", "first_name": "Ada",
                        "last_name": "Lovelace", "role": "student"}
        self.admin = {"_id": ObjectId(), "email": "support@example.com", "first_name": "Sam",
                      "last_name": "Support", "role": "admin"}
        counter = patch("apps.support.repositories.count_unread_notifications", return_value=0)
        counter.start()
        self.addCleanup(counter.stop)
        counter = patch("apps.support.repositories.count_tickets", return_value=0)
        counter.start()
        self.addCleanup(counter.stop)

    def authenticate_as(self, user):
        session = self.client.session
        session["user_id"] = str(user["_id"])
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        return patch("apps.users.decorators.get_active_user", return_value=user)

    def test_every_support_route_requires_an_authenticated_account(self):
        for url in (reverse("support:list"), reverse("support:create"), reverse("support:notifications"),
                    reverse("support:dashboard"), reverse("support:detail", args=["507f1f77bcf86cd799439011"])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("users:login"), response.url)

    def test_a_student_creates_a_ticket_and_is_redirected_to_its_detail_page(self):
        data = {"title": "Video blocked", "category": "courses", "priority": "high", "description": "Chapter 2 won't load."}
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.insert_ticket") as insert, \
             patch("apps.support.repositories.admins", return_value=[]), \
             patch("apps.support.repositories.insert_notification"):
            response = self.client.post(reverse("support:create"), data)
        self.assertEqual(response.status_code, 302)
        inserted = insert.call_args.args[0]
        self.assertEqual(inserted["title"], "Video blocked")
        self.assertEqual(inserted["status"], "open")
        self.assertEqual(response.url, reverse("support:detail", args=[str(inserted["_id"])]))

    def test_an_admin_cannot_open_a_student_ticket(self):
        with self.authenticate_as(self.admin):
            response = self.client.post(reverse("support:create"), {"title": "T", "category": "other", "priority": "low", "description": "D"})
        self.assertEqual(response.status_code, 403)

    def test_a_ticket_created_without_csrf_token_is_rejected(self):
        strict_client = Client(enforce_csrf_checks=True)
        session = strict_client.session
        session["user_id"] = str(self.student["_id"])
        session.save()
        strict_client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.insert_ticket") as insert:
            response = strict_client.post(reverse("support:create"), {"title": "T", "category": "other", "priority": "low", "description": "D"})
        self.assertEqual(response.status_code, 403)
        insert.assert_not_called()

    def test_the_dashboard_is_reserved_for_administrators(self):
        with self.authenticate_as(self.student):
            response = self.client.get(reverse("support:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_the_dashboard_summarises_the_support_activity(self):
        ticket = ticket_document()
        ticket["resolved_at"] = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.list_tickets", return_value=[ticket]), \
             patch("apps.support.repositories.count_unread_notifications", return_value=0):
            response = self.client.get(reverse("support:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Support Dashboard")
        self.assertContains(response, "Recurring issues")
        self.assertContains(response, "Average resolution time")

    def test_the_ticket_list_shows_the_unread_notification_badge(self):
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.list_tickets", return_value=[]), \
             patch("apps.support.repositories.count_unread_notifications", return_value=4):
            response = self.client.get(reverse("support:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Notifications <span class=\"sx-badge\">4</span>")
        self.assertContains(response, "4")

    def test_a_student_cannot_open_the_ticket_of_another_student(self):
        ticket = ticket_document()
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.find_ticket", return_value=ticket):
            response = self.client.get(reverse("support:detail", args=[str(ticket["_id"])]))
        self.assertEqual(response.status_code, 403)

    def test_an_invalid_ticket_identifier_returns_404(self):
        with self.authenticate_as(self.student):
            response = self.client.get(reverse("support:detail", args=["not-an-object-id"]))
        self.assertEqual(response.status_code, 404)

    def test_the_ticket_detail_renders_the_conversation_and_the_history(self):
        ticket = ticket_document(student_id=self.student["_id"], status="in_progress", history=[{"status": "open", "previous_status": None, "by": "ada@example.com", "at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc), "note": "Ticket created"}])
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.ticket_messages", return_value=[]), \
             patch("apps.support.repositories.mark_read"), \
             patch("apps.support.repositories.mark_notifications_read"), \
             patch("apps.support.repositories.count_unread_notifications", return_value=0):
            response = self.client.get(reverse("support:detail", args=[str(ticket["_id"])]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Course video")
        self.assertContains(response, "Ticket status")

    def test_an_admin_updates_the_status_priority_and_assignment(self):
        ticket = ticket_document()
        admin_entry = {"_id": self.admin["_id"], "first_name": "Sam", "last_name": "Support", "email": "support@example.com"}
        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.admins", return_value=[admin_entry]), \
             patch("apps.support.repositories.ticket_messages", return_value=[]), \
             patch("apps.support.repositories.mark_read"), \
             patch("apps.support.repositories.mark_notifications_read"), \
             patch("apps.support.repositories.count_unread_notifications", return_value=0), \
             patch("apps.support.services.set_admin_fields") as update:
            response = self.client.post(reverse("support:detail", args=[str(ticket["_id"])]), {
                "action": "admin_update", "status": "in_progress", "priority": "urgent",
                "assignee": str(self.admin["_id"]), "note": "Prise en charge"})
        self.assertEqual(response.status_code, 302)
        data = update.call_args.args[1]
        self.assertEqual(data["status"], "in_progress")
        self.assertEqual(data["priority"], "urgent")
        self.assertEqual(data["assignee"], str(self.admin["_id"]))
        self.assertEqual(data["note"], "Prise en charge")

    def test_an_admin_can_correct_the_content_of_a_ticket(self):
        ticket = ticket_document()
        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.admins", return_value=[]), \
             patch("apps.support.repositories.ticket_messages", return_value=[]), \
             patch("apps.support.repositories.mark_read"), \
             patch("apps.support.repositories.mark_notifications_read"), \
             patch("apps.support.repositories.count_unread_notifications", return_value=0), \
             patch("apps.support.services.update_ticket_content") as update:
            response = self.client.post(reverse("support:detail", args=[str(ticket["_id"])]), {
                "action": "edit", "title": "Corrected title", "category": "quizzes", "description": "Corrected description"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(update.call_args.args[1]["title"], "Corrected title")

    def test_a_student_reply_is_recorded_and_notifies_the_support_team(self):
        ticket = ticket_document(student_id=self.student["_id"])
        admin_entry = {"_id": self.admin["_id"], "first_name": "Sam", "last_name": "Support", "email": "support@example.com"}
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.insert_message") as insert_message, \
             patch("apps.support.repositories.update_ticket"), \
             patch("apps.support.repositories.admins", return_value=[admin_entry]), \
             patch("apps.support.repositories.insert_notification") as notify:
            response = self.client.post(reverse("support:detail", args=[str(ticket["_id"])]), {
                "action": "reply", "body": "Here is my screenshot."})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(insert_message.call_args.args[0]["body"], "Here is my screenshot.")
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0]["recipient_id"], self.admin["_id"])

    def test_a_student_closes_their_own_ticket(self):
        ticket = ticket_document(student_id=self.student["_id"])
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.update_ticket") as update, \
             patch("apps.support.repositories.admins", return_value=[]), \
             patch("apps.support.repositories.insert_notification"):
            response = self.client.post(reverse("support:detail", args=[str(ticket["_id"])]), {"action": "close"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(update.call_args.args[1]["status"], "resolved")

    def test_the_notification_center_lists_notifications_and_marks_them_read(self):
        notification = {"_id": ObjectId(), "recipient_id": self.student["_id"], "kind": "reply",
                        "ticket_id": ObjectId(), "title": "Support reply", "body": "Hello",
                        "created_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc), "read_at": None}
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.notifications_for", return_value=[notification]), \
             patch("apps.support.repositories.count_unread_notifications", return_value=1):
            response = self.client.get(reverse("support:notifications"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Support reply")
        self.assertContains(response, "Open ticket")

        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.mark_notifications_read") as mark:
            response = self.client.post(reverse("support:notifications"))
        self.assertEqual(response.status_code, 302)
        mark.assert_called_once()

    def test_deleting_a_ticket_requires_a_post_request(self):
        ticket = ticket_document()
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.find_ticket", return_value=ticket):
            response = self.client.get(reverse("support:delete", args=[str(ticket["_id"])]))
        self.assertEqual(response.status_code, 405)

    def test_a_message_without_attachment_cannot_be_downloaded(self):
        ticket = ticket_document(student_id=self.student["_id"])
        message = {"_id": ObjectId(), "ticket_id": ticket["_id"], "body": "Hello", "attachment": None}
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.messages.find_one", return_value=message):
            response = self.client.get(reverse("support:attachment", args=[str(ticket["_id"]), str(message["_id"])]))
        self.assertEqual(response.status_code, 404)

    def test_the_support_module_exposes_its_documented_routes(self):
        self.assertEqual(reverse("support:list"), "/support/")
        self.assertEqual(reverse("support:create"), "/support/new/")
        self.assertEqual(reverse("support:dashboard"), "/support/dashboard/")
        self.assertEqual(reverse("support:notifications"), "/support/notifications/")
        self.assertEqual(reverse("support:faq"), "/support/faq/")


class QueueFilterViewTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        self.admin = {"_id": ObjectId(), "email": "support@example.com", "first_name": "Sam",
                      "last_name": "Support", "role": "admin"}

    def authenticate_as(self, user):
        session = self.client.session
        session["user_id"] = str(user["_id"])
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        return patch("apps.users.decorators.get_active_user", return_value=user)

    def test_the_back_office_queue_applies_the_filters(self):
        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.list_tickets", return_value=[]) as listed, \
             patch("apps.support.repositories.count_tickets", return_value=4), \
             patch("apps.support.repositories.admins", return_value=[]), \
             patch("apps.support.repositories.count_unread_notifications", return_value=0):
            response = self.client.get(reverse("support:list"), {"status": "open", "priority": "high", "q": "video"})
        self.assertEqual(response.status_code, 200)
        query = listed.call_args.args[0]
        self.assertEqual(query["status"], "open")
        self.assertEqual(query["priority"], "high")
        self.assertIn("$or", query)
        self.assertContains(response, "<strong>4</strong>")
        self.assertContains(response, "tickets in the queue")

    def test_the_queue_can_be_sorted_by_priority(self):
        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.list_tickets", return_value=[]) as listed, \
             patch("apps.support.repositories.count_tickets", return_value=0), \
             patch("apps.support.repositories.admins", return_value=[]), \
             patch("apps.support.repositories.count_unread_notifications", return_value=0):
            self.client.get(reverse("support:list"), {"sort": "priority"})
        self.assertTrue(any(call.kwargs.get("sort") == "priority" for call in listed.call_args_list))

    def test_a_student_cannot_filter_on_the_assignment(self):
        student = {"_id": ObjectId(), "email": "ada@example.com", "first_name": "Ada",
                   "last_name": "Lovelace", "role": "student"}
        with self.authenticate_as(student), \
             patch("apps.support.repositories.list_tickets", return_value=[]) as listed, \
             patch("apps.support.repositories.count_tickets", return_value=0), \
             patch("apps.support.repositories.count_unread_notifications", return_value=0):
            response = self.client.get(reverse("support:list"), {"assignee": "me"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("assignee_id", listed.call_args_list[0].args[0])
        self.assertNotContains(response, 'name="assignee"')


class InternalNoteViewTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        self.student = {"_id": ObjectId(), "email": "ada@example.com", "first_name": "Ada",
                        "last_name": "Lovelace", "role": "student"}
        self.admin = {"_id": ObjectId(), "email": "support@example.com", "first_name": "Sam",
                      "last_name": "Support", "role": "admin"}

    def authenticate_as(self, user):
        session = self.client.session
        session["user_id"] = str(user["_id"])
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        return patch("apps.users.decorators.get_active_user", return_value=user)

    def test_the_student_never_sees_the_internal_notes(self):
        ticket = ticket_document(student_id=self.student["_id"])
        note = {"_id": ObjectId(), "ticket_id": ticket["_id"], "author_id": self.admin["_id"],
                "author_name": "Sam Support", "body": "Le fournisseur est en panne.", "created_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)}
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.ticket_messages", return_value=[]), \
             patch("apps.support.repositories.mark_read"), \
             patch("apps.support.repositories.mark_notifications_read"), \
             patch("apps.support.repositories.count_unread_notifications", return_value=0), \
             patch("apps.support.repositories.ticket_notes", return_value=[note]):
            response = self.client.get(reverse("support:detail", args=[str(ticket["_id"])]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Le fournisseur est en panne.")
        self.assertNotContains(response, "Notes internes")

    def test_the_admin_reads_and_adds_the_internal_notes(self):
        ticket = ticket_document()
        note = {"_id": ObjectId(), "ticket_id": ticket["_id"], "author_id": self.admin["_id"],
                "author_name": "Sam Support", "body": "Le fournisseur est en panne.", "created_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)}
        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.admins", return_value=[]), \
             patch("apps.support.repositories.ticket_messages", return_value=[]), \
             patch("apps.support.repositories.mark_read"), \
             patch("apps.support.repositories.mark_notifications_read"), \
             patch("apps.support.repositories.count_unread_notifications", return_value=0), \
             patch("apps.support.repositories.ticket_notes", return_value=[note]):
            response = self.client.get(reverse("support:detail", args=[str(ticket["_id"])]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Internal notes")
        self.assertContains(response, "Le fournisseur est en panne.")

        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.insert_note") as insert:
            response = self.client.post(reverse("support:detail", args=[str(ticket["_id"])]), {"action": "note", "body": "Relance du fournisseur."})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(insert.call_args.args[0]["body"], "Relance du fournisseur.")

    def test_a_student_cannot_post_an_internal_note(self):
        ticket = ticket_document(student_id=self.student["_id"])
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.insert_note") as insert:
            response = self.client.post(reverse("support:detail", args=[str(ticket["_id"])]), {"action": "note", "body": "Note parasite"})
        self.assertEqual(response.status_code, 403)
        insert.assert_not_called()

    def test_only_the_author_deletes_an_internal_note(self):
        ticket = ticket_document()
        note = {"_id": ObjectId(), "ticket_id": ticket["_id"], "author_id": self.admin["_id"], "body": "Note"}
        url = reverse("support:note_delete", args=[str(ticket["_id"]), str(note["_id"])])
        with self.authenticate_as(self.student), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.delete_note") as delete:
            response = self.client.post(url)
        self.assertEqual(response.status_code, 403)
        delete.assert_not_called()
        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.find_note", return_value=note), \
             patch("apps.support.repositories.delete_note") as delete:
            response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        delete.assert_called_once_with(note["_id"])


class KnowledgeBaseViewTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        self.student = {"_id": ObjectId(), "email": "ada@example.com", "first_name": "Ada",
                        "last_name": "Lovelace", "role": "student"}
        self.admin = {"_id": ObjectId(), "email": "support@example.com", "first_name": "Sam",
                      "last_name": "Support", "role": "admin"}
        self.entry = {"_id": ObjectId(), "question": "A video won't load",
                      "answer": "Reload the course page.", "keywords": ["video"],
                      "category": "courses", "is_published": True}

    def authenticate_as(self, user):
        session = self.client.session
        session["user_id"] = str(user["_id"])
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        return patch("apps.users.decorators.get_active_user", return_value=user)

    def test_the_knowledge_base_is_managed_by_administrators_only(self):
        for url in (reverse("support:faq"), reverse("support:faq_create")):
            with self.subTest(url=url):
                with self.authenticate_as(self.student):
                    self.assertEqual(self.client.get(url).status_code, 403)
                with self.authenticate_as(self.admin), \
                     patch("apps.support.repositories.list_faq", return_value=[self.entry]):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_admin_lists_and_publishes_a_solution(self):
        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.list_faq", return_value=[self.entry]):
            response = self.client.get(reverse("support:faq"))
        self.assertContains(response, "A video")
        self.assertContains(response, "Published")

        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.insert_faq") as insert:
            response = self.client.post(reverse("support:faq_create"), {
                "question": "How to change my password?", "answer": "From the sign-in page.",
                "keywords": "password, login", "category": "account", "is_published": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(insert.call_args.args[0]["keywords"], ["password", "login"])

    def test_the_admin_edits_a_solution(self):
        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.find_faq", return_value=self.entry), \
             patch("apps.support.repositories.update_faq", return_value=True) as update:
            response = self.client.post(reverse("support:faq_edit", args=[str(self.entry["_id"])]), {
                "question": "A video no longer plays", "answer": "Clear the cache.", "keywords": "video",
                "category": "courses", "is_published": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(update.call_args.args[1]["question"], "A video no longer plays")

    def test_the_admin_deletes_a_solution(self):
        with self.authenticate_as(self.admin), \
             patch("apps.support.repositories.delete_faq", return_value=True) as delete:
            response = self.client.post(reverse("support:faq_delete", args=[str(self.entry["_id"])]))
        self.assertEqual(response.status_code, 302)
        delete.assert_called_once_with(self.entry["_id"])
