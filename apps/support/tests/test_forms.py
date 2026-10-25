from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from apps.support.forms import AdminTicketForm, FaqForm, NoteForm, ReplyForm, TicketFilterForm, TicketForm


class TicketFormTests(SimpleTestCase):
    def test_a_complete_ticket_is_valid(self):
        form = TicketForm({"title": "Video blocked", "category": "courses", "priority": "high", "description": "Chapter 2 won't load."})
        self.assertTrue(form.is_valid(), form.errors)

    def test_title_description_category_and_priority_are_required(self):
        form = TicketForm({})
        self.assertFalse(form.is_valid())
        for field in ("title", "category", "priority", "description"):
            self.assertIn(field, form.errors)

    def test_unknown_category_and_priority_are_rejected(self):
        form = TicketForm({"title": "T", "category": "unknown", "priority": "critical", "description": "D"})
        self.assertFalse(form.is_valid())
        self.assertIn("category", form.errors)
        self.assertIn("priority", form.errors)


class ReplyFormTests(SimpleTestCase):
    def test_a_message_without_attachment_is_valid(self):
        form = ReplyForm({"body": "Hello, here is my screenshot."})
        self.assertTrue(form.is_valid(), form.errors)

    def test_an_empty_message_is_rejected(self):
        form = ReplyForm({"body": "   "})
        self.assertFalse(form.is_valid())

    def test_screenshots_and_documents_are_accepted(self):
        for name in ("screenshot.png", "photo.jpg", "proof.webp", "schema.gif", "note.pdf", "text.txt"):
            with self.subTest(name=name):
                upload = SimpleUploadedFile(name, b"payload", content_type="application/octet-stream")
                form = ReplyForm({"body": "Hello"}, {"attachment": upload})
                self.assertTrue(form.is_valid(), form.errors)

    def test_an_executable_file_is_rejected(self):
        upload = SimpleUploadedFile("payload.exe", b"payload", content_type="application/octet-stream")
        form = ReplyForm({"body": "Hello"}, {"attachment": upload})
        self.assertFalse(form.is_valid())
        self.assertIn("attachment", form.errors)

    def test_an_oversized_file_is_rejected(self):
        upload = SimpleUploadedFile("screenshot.png", b"0" * (5 * 1024 * 1024 + 1), content_type="image/png")
        form = ReplyForm({"body": "Hello"}, {"attachment": upload})
        self.assertFalse(form.is_valid())
        self.assertIn("attachment", form.errors)


class AdminTicketFormTests(SimpleTestCase):
    def test_admin_directories_become_the_assignee_choices(self):
        form = AdminTicketForm({"status": "open", "priority": "normal", "assignee": "", "note": ""},
                               admins=[{"_id": "abc", "first_name": "Sam", "last_name": "Support", "email": "support@example.com"}])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual([choice[0] for choice in form.fields["assignee"].choices], ["", "abc"])

    def test_the_follow_up_note_is_optional(self):
        form = AdminTicketForm({"status": "in_progress", "priority": "high", "assignee": ""}, admins=[])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["note"], "")

    def test_an_unknown_status_is_rejected(self):
        form = AdminTicketForm({"status": "closed", "priority": "normal", "assignee": ""}, admins=[])
        self.assertFalse(form.is_valid())
        self.assertIn("status", form.errors)


class TicketFilterFormTests(SimpleTestCase):
    def test_a_student_filter_only_exposes_search_status_and_category(self):
        form = TicketFilterForm({"q": "video", "status": "open"}, user={"role": "student"})
        self.assertIn("q", form.fields)
        self.assertIn("status", form.fields)
        self.assertIn("category", form.fields)
        self.assertNotIn("priority", form.fields)
        self.assertNotIn("assignee", form.fields)
        self.assertNotIn("sort", form.fields)

    def test_an_admin_filter_exposes_the_assignment_and_sorting_options(self):
        form = TicketFilterForm({"assignee": "me", "sort": "priority"},
                                 user={"role": "admin"},
                                 admins=[{"_id": "abc", "first_name": "Sam", "last_name": "Support"}])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["assignee"], "me")
        self.assertEqual(form.cleaned_data["sort"], "priority")
        choices = [choice[0] for choice in form.fields["assignee"].choices]
        self.assertEqual(choices[:3], ["", "me", "unassigned"])
        self.assertIn("abc", choices)

    def test_the_filters_are_returned_as_a_clean_dictionary(self):
        form = TicketFilterForm({"q": "  video  ", "status": "open", "priority": ""},
                                 user={"role": "admin"}, admins=[])
        self.assertTrue(form.is_valid(), form.errors)
        filters = form.filters()
        self.assertEqual(filters["q"], "video")
        self.assertEqual(filters["status"], "open")
        self.assertNotIn("priority", filters)


class NoteFormTests(SimpleTestCase):
    def test_an_internal_note_requires_a_body(self):
        form = NoteForm({"body": "  "})
        self.assertFalse(form.is_valid())
        self.assertIn("body", form.errors)

    def test_an_internal_note_is_valid(self):
        form = NoteForm({"body": "Provider contacted."})
        self.assertTrue(form.is_valid(), form.errors)


class FaqFormTests(SimpleTestCase):
    def test_keywords_are_split_into_a_list(self):
        form = FaqForm({"question": "How to sign in?", "answer": "Use your email.",
                        "keywords": "password, login , email", "category": "account", "is_published": "on"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["keywords"], ["password", "login", "email"])
        self.assertTrue(form.cleaned_data["is_published"])

    def test_a_question_and_an_answer_are_required(self):
        form = FaqForm({})
        self.assertFalse(form.is_valid())
        self.assertIn("question", form.errors)
        self.assertIn("answer", form.errors)

    def test_an_unknown_category_is_rejected(self):
        form = FaqForm({"question": "Q", "answer": "A", "keywords": "", "category": "unknown"})
        self.assertFalse(form.is_valid())
        self.assertIn("category", form.errors)
