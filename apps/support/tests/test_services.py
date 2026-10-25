from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bson import ObjectId
from django.test import SimpleTestCase
from pymongo.errors import PyMongoError

from apps.support import services


def make_ticket(**overrides):
    ticket = {
        "_id": ObjectId(),
        "student_id": ObjectId(),
        "student_name": "Ada Lovelace",
        "student_email": "ada@example.com",
        "title": "Course video won't load",
        "description": "",
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


def make_user(role="student", **overrides):
    user = {"_id": ObjectId(), "email": "ada@example.com", "first_name": "Ada",
            "last_name": "Lovelace", "role": role}
    user.update(overrides)
    return user


class TicketCreationTests(SimpleTestCase):
    def test_create_ticket_stores_document_and_notifies_admins(self):
        user = make_user()
        admin = make_user(role="admin", email="support@example.com")
        data = {"title": "Video blocked", "description": "Cannot play", "category": "courses", "priority": "high"}
        with patch("apps.support.repositories.insert_ticket") as insert, \
             patch("apps.support.repositories.admins", return_value=[admin]), \
             patch("apps.support.repositories.insert_notification") as notify:
            ticket = services.create_ticket(data, user)
        stored = insert.call_args.args[0]
        self.assertEqual(stored["status"], "open")
        self.assertEqual(stored["assignee_id"], None)
        self.assertEqual(stored["student_id"], user["_id"])
        self.assertEqual(stored["history"][0]["status"], "open")
        self.assertEqual(stored["history"][0]["previous_status"], None)
        self.assertEqual(ticket["_id"], stored["_id"])
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0]["recipient_id"], admin["_id"])
        self.assertEqual(notify.call_args.args[0]["kind"], "ticket_created")

    def test_create_ticket_survives_an_unavailable_admin_directory(self):
        user = make_user()
        with patch("apps.support.repositories.insert_ticket"), \
             patch("apps.support.repositories.admins", side_effect=PyMongoError("mongo down")), \
             patch("apps.support.repositories.insert_notification"):
            ticket = services.create_ticket({"title": "T", "description": "D", "category": "other", "priority": "low"}, user)
        self.assertEqual(ticket["status"], "open")


class TicketUpdateTests(SimpleTestCase):
    def test_owner_can_update_title_description_and_category(self):
        ticket = make_ticket()
        user = make_user(_id=ticket["student_id"])
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.update_ticket") as update:
            services.update_ticket_content(str(ticket["_id"]), {"title": "Nouveau titre", "description": "Nouvelle description", "category": "quizzes"}, user)
        fields = update.call_args.args[1]
        self.assertEqual(fields["title"], "Nouveau titre")
        self.assertEqual(fields["description"], "Nouvelle description")
        self.assertEqual(fields["category"], "quizzes")
        self.assertNotIn("status", fields)

    def test_student_cannot_edit_a_resolved_ticket_but_admin_can(self):
        ticket = make_ticket(status="resolved")
        student = make_user(_id=ticket["student_id"])
        with patch("apps.support.repositories.find_ticket", return_value=ticket):
            with self.assertRaises(services.TicketPermissionDenied):
                services.update_ticket_content(str(ticket["_id"]), {"title": "T", "description": "D", "category": "other"}, student)
        admin = make_user(role="admin")
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.update_ticket") as update:
            services.update_ticket_content(str(ticket["_id"]), {"title": "T", "description": "D", "category": "other"}, admin)
        update.assert_called_once()

    def test_student_cannot_read_or_edit_another_students_ticket(self):
        ticket = make_ticket()
        other = make_user()
        with patch("apps.support.repositories.find_ticket", return_value=ticket):
            with self.assertRaises(services.TicketPermissionDenied):
                services.get_ticket(str(ticket["_id"]), other)
            with self.assertRaises(services.TicketPermissionDenied):
                services.update_ticket_content(str(ticket["_id"]), {"title": "T", "description": "D", "category": "other"}, other)

    def test_close_ticket_records_history_and_notifies_support(self):
        ticket = make_ticket()
        student = make_user(_id=ticket["student_id"])
        admin = make_user(role="admin", email="support@example.com")
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.update_ticket") as update, \
             patch("apps.support.repositories.admins", return_value=[admin]), \
             patch("apps.support.repositories.insert_notification") as notify:
            services.close_student_ticket(str(ticket["_id"]), student)
        fields = update.call_args.args[1]
        self.assertEqual(fields["status"], "resolved")
        self.assertIsNotNone(fields["resolved_at"])
        self.assertEqual(fields["history"][-1]["previous_status"], "open")
        notify.assert_called_once()

    def test_delete_is_refused_when_the_ticket_already_has_messages(self):
        ticket = make_ticket()
        student = make_user(_id=ticket["student_id"])
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.messages.count_documents", return_value=2), \
             patch("apps.support.repositories.remove_ticket") as remove:
            with self.assertRaises(services.TicketPermissionDenied):
                services.delete_student_ticket(str(ticket["_id"]), student)
        remove.assert_not_called()

    def test_delete_is_allowed_for_an_admin_and_removes_the_ticket(self):
        ticket = make_ticket()
        admin = make_user(role="admin")
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.remove_ticket") as remove:
            services.delete_student_ticket(str(ticket["_id"]), admin)
        remove.assert_called_once_with(ticket["_id"])


class AdminHandlingTests(SimpleTestCase):
    def test_status_priority_and_assignment_are_updated_with_history(self):
        ticket = make_ticket(status="open")
        admin = make_user(role="admin")
        other_admin = make_user(role="admin", email="sophie@example.com", first_name="Sophie")
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.admins", return_value=[other_admin]), \
             patch("apps.support.repositories.update_ticket") as update, \
             patch("apps.support.repositories.insert_notification") as notify:
            services.set_admin_fields(str(ticket["_id"]), {"status": "in_progress", "priority": "urgent", "assignee": str(other_admin["_id"]), "note": "Prise en charge"}, admin)
        fields = update.call_args.args[1]
        self.assertEqual(fields["status"], "in_progress")
        self.assertEqual(fields["priority"], "urgent")
        self.assertEqual(fields["assignee_id"], other_admin["_id"])
        self.assertEqual(fields["assignee_name"], "Sophie Lovelace")
        self.assertIsNone(fields["resolved_at"])
        self.assertEqual(fields["history"][-1]["previous_status"], "open")
        self.assertEqual(fields["history"][-1]["note"], "Prise en charge")
        notified = {call.args[0]["recipient_id"] for call in notify.call_args_list}
        self.assertIn(ticket["student_id"], notified)
        self.assertIn(other_admin["_id"], notified)

    def test_resolving_a_ticket_stamps_the_resolution_date(self):
        ticket = make_ticket(status="in_progress")
        admin = make_user(role="admin")
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.admins", return_value=[]), \
             patch("apps.support.repositories.update_ticket") as update, \
             patch("apps.support.repositories.insert_notification"):
            services.set_admin_fields(str(ticket["_id"]), {"status": "resolved", "priority": "normal", "assignee": "", "note": ""}, admin)
        self.assertIsNotNone(update.call_args.args[1]["resolved_at"])

    def test_a_malformed_assignee_is_rejected(self):
        ticket = make_ticket()
        admin = make_user(role="admin")
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.admins", return_value=[]), \
             patch("apps.support.repositories.update_ticket") as update:
            with self.assertRaises(services.TicketPermissionDenied):
                services.set_admin_fields(str(ticket["_id"]), {"status": "open", "priority": "normal", "assignee": "not-an-object-id", "note": ""}, admin)
        update.assert_not_called()


class ReplyTests(SimpleTestCase):
    def test_admin_reply_moves_the_ticket_in_progress_and_notifies_the_student(self):
        ticket = make_ticket()
        admin = make_user(role="admin")
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.insert_message") as insert, \
             patch("apps.support.repositories.update_ticket") as update, \
             patch("apps.support.repositories.insert_notification") as notify:
            services.reply(str(ticket["_id"]), "We checked your video.", None, admin)
        message = insert.call_args.args[0]
        self.assertEqual(message["body"], "We checked your video.")
        self.assertEqual(message["sender_role"], "admin")
        self.assertEqual(message["recipient_id"], ticket["student_id"])
        self.assertEqual(update.call_args.args[1]["status"], "in_progress")
        self.assertEqual(update.call_args.args[1]["history"][-1]["previous_status"], "open")
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0]["recipient_id"], ticket["student_id"])
        self.assertEqual(notify.call_args.args[0]["kind"], "reply")

    def test_student_reply_reopens_a_waiting_ticket_and_notifies_support(self):
        ticket = make_ticket(status="waiting")
        student = make_user(_id=ticket["student_id"])
        admin = make_user(role="admin", email="support@example.com")
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.insert_message"), \
             patch("apps.support.repositories.update_ticket") as update, \
             patch("apps.support.repositories.admins", return_value=[admin]), \
             patch("apps.support.repositories.insert_notification") as notify:
            services.reply(str(ticket["_id"]), "Merci pour votre retour.", None, student)
        fields = update.call_args.args[1]
        self.assertEqual(fields["status"], "open")
        self.assertIsNone(fields["resolved_at"])
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0]["recipient_id"], admin["_id"])

    def test_student_reply_without_assignee_warns_every_admin(self):
        ticket = make_ticket(status="open")
        student = make_user(_id=ticket["student_id"])
        first = make_user(role="admin", email="a@example.com")
        second = make_user(role="admin", email="b@example.com")
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.insert_message"), \
             patch("apps.support.repositories.update_ticket"), \
             patch("apps.support.repositories.admins", return_value=[first, second]), \
             patch("apps.support.repositories.insert_notification") as notify:
            services.reply(str(ticket["_id"]), "Bonjour.", None, student)
        self.assertEqual(notify.call_count, 2)


class NotificationTests(SimpleTestCase):
    def test_unread_count_and_mark_as_read_use_the_recipient(self):
        user = make_user()
        with patch("apps.support.repositories.count_unread_notifications", return_value=3) as count:
            self.assertEqual(services.unread_notification_count(user), 3)
        count.assert_called_once_with(user["_id"])
        with patch("apps.support.repositories.mark_notifications_read", return_value=2) as mark:
            services.mark_notifications_read(user)
        mark.assert_called_once()
        self.assertEqual(mark.call_args.kwargs["ticket_id"], None)

    def test_notifications_can_be_read_for_a_single_ticket(self):
        user = make_user()
        ticket = make_ticket()
        with patch("apps.support.repositories.mark_notifications_read", return_value=1) as mark:
            services.mark_notifications_read(user, ticket_id=ticket["_id"])
        self.assertEqual(mark.call_args.kwargs["ticket_id"], ticket["_id"])

    def test_notify_ignores_a_missing_recipient(self):
        ticket = make_ticket()
        with patch("apps.support.repositories.insert_notification") as insert:
            self.assertIsNone(services.notify(None, "reply", ticket, "Titre", "Corps"))
        insert.assert_not_called()


class DashboardTests(SimpleTestCase):
    def test_dashboard_counts_tickets_by_status_category_and_priority(self):
        tickets = [
            make_ticket(status="open", category="courses", priority="high"),
            make_ticket(status="in_progress", category="courses", priority="low"),
            make_ticket(status="resolved", category="quizzes", priority="normal",
                        resolved_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)),
        ]
        stats = services.dashboard_stats(tickets)
        self.assertEqual(stats["total_count"], 3)
        self.assertEqual(stats["open_count"], 2)
        self.assertEqual(stats["resolved_count"], 1)
        self.assertEqual(stats["in_progress_count"], 1)
        self.assertEqual(stats["unassigned_count"], 2)
        self.assertEqual(dict(stats["categories"])["courses"], 2)
        self.assertEqual(dict(stats["priorities"])["high"], 1)
        self.assertEqual(stats["avg_resolution_hours"], 2.0)
        self.assertEqual(stats["median_resolution_hours"], 2.0)

    def test_dashboard_is_empty_without_resolved_tickets(self):
        stats = services.dashboard_stats([make_ticket(status="open")])
        self.assertIsNone(stats["avg_resolution_hours"])
        self.assertIsNone(stats["median_resolution_hours"])
        self.assertEqual(stats["resolved_count"], 0)

    def test_dashboard_detects_recurring_problems_across_titles(self):
        first = make_ticket(title="Course video won't load", category="courses")
        second = make_ticket(title="Cannot load course video", category="courses")
        third = make_ticket(title="Password forgotten", category="account")
        stats = services.dashboard_stats([first, second, third])
        self.assertEqual(stats["recurring_count"], 1)
        recurring = stats["recurring"][0]
        self.assertEqual(recurring["count"], 2)
        self.assertEqual(recurring["label"], "Video / course playback")
        self.assertEqual(stats["recurring_ticket_count"], 2)
        self.assertEqual(stats["recurring_rate"], 66.7)

    def test_recurring_problems_report_the_number_of_impacted_students(self):
        student = ObjectId()
        tickets = [
            make_ticket(title="Le quiz ne s'ouvre pas", student_id=student),
            make_ticket(title="Le quiz ne s'ouvre pas sur mon telephone", student_id=student),
            make_ticket(title="Quiz impossible a lancer", student_id=ObjectId()),
        ]
        recurring = services.recurring_problems(tickets)
        self.assertEqual(len(recurring), 1)
        self.assertEqual(recurring[0]["count"], 3)
        self.assertEqual(recurring[0]["student_count"], 2)

    def test_recurring_problems_ignore_unique_issues(self):
        self.assertEqual(services.recurring_problems([make_ticket(title="Unique problem")]), [])


class SignatureTests(SimpleTestCase):
    def test_theme_detection_uses_the_most_frequent_keywords(self):
        self.assertEqual(services.detect_theme("Course video won't load"), "video")
        self.assertEqual(services.detect_theme("I forgot my password"), "compte")
        self.assertEqual(services.detect_theme("My quiz won't open"), "quiz")
        self.assertIsNone(services.detect_theme("Bonjour"))

    def test_signature_is_stable_for_similar_wording(self):
        first = services.problem_signature("Course video won't load", "Video blocked")
        second = services.problem_signature("Cannot load course video", "Video blocked")
        self.assertEqual(first, second)

    def test_signature_falls_back_on_meaningful_words(self):
        signature = services.problem_signature("Synchronisation du classeur impossible", "")
        self.assertIsNotNone(signature)
        self.assertIn("classeur", signature.split())
        self.assertNotIn("impossible", signature.split())

    def test_normalize_text_removes_accents_and_punctuation(self):
        self.assertEqual(services.normalize_text("Video : ne charge-pas !"), "video ne charge-pas")


class QueueFilterTests(SimpleTestCase):
    def test_a_student_only_ever_filters_their_own_tickets(self):
        student = make_user()
        query = services.build_ticket_query(student, {"status": "open", "q": "video"})
        self.assertEqual(query, {"student_id": student["_id"]})

    def test_an_admin_filters_by_status_priority_category_and_assignee(self):
        admin = make_user(role="admin")
        query = services.build_ticket_query(admin, {"status": "open", "priority": "high", "category": "courses"})
        self.assertEqual(query["status"], "open")
        self.assertEqual(query["priority"], "high")
        self.assertEqual(query["category"], "courses")

        query = services.build_ticket_query(admin, {"assignee": "me"})
        self.assertEqual(query["assignee_id"], admin["_id"])
        query = services.build_ticket_query(admin, {"assignee": "unassigned"})
        self.assertIsNone(query["assignee_id"])

    def test_a_free_text_search_targets_the_title_and_the_description(self):
        admin = make_user(role="admin")
        query = services.build_ticket_query(admin, {"q": "video"})
        self.assertEqual(len(query["$or"]), 2)
        self.assertIn("video", query["$or"][0]["title"]["$regex"])

    def test_a_regex_metacharacter_in_a_search_is_escaped(self):
        admin = make_user(role="admin")
        query = services.build_ticket_query(admin, {"q": "a.*b"})
        self.assertEqual(query["$or"][0]["title"]["$regex"], "a\\.\\*b")

    def test_pagination_returns_the_queue_metadata(self):
        admin = make_user(role="admin")
        with patch("apps.support.repositories.count_tickets", return_value=45) as count, \
             patch("apps.support.repositories.list_tickets", return_value=[make_ticket()]) as listed:
            queue = services.paginate_tickets(admin, {}, page=2, per_page=20)
        self.assertEqual(queue["total"], 45)
        self.assertEqual(queue["page"], 2)
        self.assertEqual(queue["pages"], 3)
        self.assertEqual(listed.call_args.kwargs["skip"], 20)
        self.assertEqual(listed.call_args.kwargs["limit"], 20)

    def test_pagination_clamps_an_out_of_range_page(self):
        admin = make_user(role="admin")
        with patch("apps.support.repositories.count_tickets", return_value=5), \
             patch("apps.support.repositories.list_tickets", return_value=[]):
            queue = services.paginate_tickets(admin, {}, page=99, per_page=20)
        self.assertEqual(queue["page"], 1)
        self.assertEqual(queue["pages"], 1)


class InternalNoteTests(SimpleTestCase):
    def test_an_admin_adds_an_internal_note(self):
        ticket = make_ticket()
        admin = make_user(role="admin")
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.insert_note") as insert:
            note = services.add_note(str(ticket["_id"]), "Le fournisseur a un incident en cours.", admin)
        document = insert.call_args.args[0]
        self.assertEqual(document["body"], "Le fournisseur a un incident en cours.")
        self.assertEqual(document["author_id"], admin["_id"])
        self.assertEqual(note["ticket_id"], ticket["_id"])

    def test_a_student_cannot_add_an_internal_note(self):
        ticket = make_ticket()
        student = make_user(_id=ticket["student_id"])
        with patch("apps.support.repositories.find_ticket", return_value=ticket), \
             patch("apps.support.repositories.insert_note") as insert:
            with self.assertRaises(services.TicketPermissionDenied):
                services.add_note(str(ticket["_id"]), "Note parasite", student)
        insert.assert_not_called()

    def test_only_the_author_can_delete_an_internal_note(self):
        ticket = make_ticket()
        author = make_user(role="admin")
        colleague = make_user(role="admin", email="colleague@example.com")
        note = {"_id": ObjectId(), "ticket_id": ticket["_id"], "author_id": author["_id"], "body": "Note"}
        with patch("apps.support.repositories.find_note", return_value=note), \
             patch("apps.support.repositories.delete_note") as delete:
            with self.assertRaises(services.TicketPermissionDenied):
                services.delete_note(str(note["_id"]), str(ticket["_id"]), colleague)
        delete.assert_not_called()
        with patch("apps.support.repositories.find_note", return_value=note), \
             patch("apps.support.repositories.delete_note") as delete:
            services.delete_note(str(note["_id"]), str(ticket["_id"]), author)
        delete.assert_called_once_with(note["_id"])

    def test_notes_are_listed_for_a_ticket(self):
        ticket = make_ticket()
        with patch("apps.support.repositories.ticket_notes", return_value=[{"_id": ObjectId(), "body": "Note"}]) as listed:
            notes = services.notes_for(ticket)
        listed.assert_called_once_with(ticket["_id"])
        self.assertEqual(len(notes), 1)


class KnowledgeBaseTests(SimpleTestCase):
    def test_the_built_in_entries_are_used_when_no_solution_is_stored(self):
        with patch("apps.support.repositories.list_faq", return_value=[]):
            entries = services.faq_entries()
        self.assertTrue(entries)
        self.assertEqual(entries[0]["is_published"], True)

    def test_stored_solutions_take_precedence_over_the_built_in_ones(self):
        stored = {"_id": ObjectId(), "question": "Question", "answer": "Answer", "keywords": [], "category": "other"}
        with patch("apps.support.repositories.list_faq", return_value=[stored]):
            entries = services.faq_entries()
        self.assertEqual(entries, [stored])

    def test_a_solution_is_created_updated_and_deleted(self):
        admin = make_user(role="admin")
        data = {"question": "Comment changer mon mot de passe ?", "answer": "Depuis la page de connexion.",
                "keywords": ["mot de passe"], "category": "account", "is_published": True}
        with patch("apps.support.repositories.insert_faq") as insert:
            entry = services.create_faq(data, admin)
        stored = insert.call_args.args[0]
        self.assertEqual(stored["question"], data["question"])
        self.assertEqual(stored["keywords"], ["mot de passe"])
        with patch("apps.support.repositories.update_faq", return_value=True) as update:
            services.update_faq(entry["_id"], dict(data, question="Nouvelle question"), admin)
        self.assertEqual(update.call_args.args[1]["question"], "Nouvelle question")
        with patch("apps.support.repositories.delete_faq", return_value=True) as delete:
            services.delete_faq(entry["_id"])
        delete.assert_called_once_with(entry["_id"])

    def test_the_search_ranks_the_matching_solution_first(self):
        entries = [{"_id": ObjectId(), "question": "A video won't load", "answer": "Reload the page.",
                    "keywords": ["video", "load"], "category": "courses"},
                   {"_id": ObjectId(), "question": "Password forgotten", "answer": "Reset it.",
                    "keywords": ["password"], "category": "account"}]
        with patch("apps.support.repositories.list_faq", return_value=entries):
            results = services.search_faq("the video won't load")
        self.assertEqual(results[0]["question"], "A video won't load")

    def test_the_search_can_be_restricted_to_a_category(self):
        entries = [{"_id": ObjectId(), "question": "Quiz blocked", "answer": "Clear the cache.",
                    "keywords": ["quiz"], "category": "quizzes"},
                   {"_id": ObjectId(), "question": "Quiz de python", "answer": "Relancez le quiz.",
                    "keywords": ["quiz", "python"], "category": "courses"}]
        with patch("apps.support.repositories.list_faq", return_value=entries):
            results = services.search_faq("quiz", category="quizzes")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["category"], "quizzes")

    def test_a_search_without_match_returns_nothing(self):
        entries = [{"_id": ObjectId(), "question": "Une vidéo ne se charge pas", "answer": "Rechargez.",
                    "keywords": ["vidéo"], "category": "courses"}]
        with patch("apps.support.repositories.list_faq", return_value=entries):
            self.assertEqual(services.search_faq("problème de facturation inconnu"), [])

    def test_the_default_solutions_are_seeded_once(self):
        with patch("apps.support.repositories.count_faq", return_value=0), \
             patch("apps.support.repositories.insert_faq") as insert:
            created = services.seed_default_faq()
        self.assertEqual(created, len(services.DEFAULT_FAQ))
        self.assertEqual(insert.call_count, len(services.DEFAULT_FAQ))
        with patch("apps.support.repositories.count_faq", return_value=4), \
             patch("apps.support.repositories.insert_faq") as insert:
            self.assertEqual(services.seed_default_faq(), 0)
        insert.assert_not_called()
