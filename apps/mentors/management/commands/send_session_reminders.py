from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand

from apps.mentors import email_service, notifications
from apps.mentors.bookings import STATUS_ACCEPTED, bookings_collection
from config.mongodb import db

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Dispatch notifications and emails for upcoming confirmed mentorship sessions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Lookahead window in hours for upcoming sessions (default: 24).",
        )

    def handle(self, *args, **options):
        hours = options["hours"]
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours)

        today_str = now.strftime("%Y-%m-%d")
        cutoff_date_str = cutoff.strftime("%Y-%m-%d")

        query = {
            "status": STATUS_ACCEPTED,
            "reminder_sent": {"$ne": True},
            "date": {"$gte": today_str, "$lte": cutoff_date_str},
        }

        sessions = list(bookings_collection.find(query))
        self.stdout.write(f"Found {len(sessions)} confirmed sessions due for reminder.")

        reminded_count = 0
        for session in sessions:
            booking_id = session["_id"]
            student_id = session.get("student_id")
            mentor_id = session.get("mentor_id")
            student_name = session.get("student_name", "Student")
            mentor_name = session.get("mentor_name", "Mentor")
            topic = session.get("topic", "Mentorship")
            date_str = session.get("date", "")
            time_str = session.get("start_time", "")

            # In-app notification to student
            notifications.create_notification(
                recipient_id=student_id,
                event_type="session_reminder",
                title="Reminder: Upcoming Coaching Session",
                message=f"You have an upcoming session on {topic} with {mentor_name} on {date_str} at {time_str}.",
                link_url="/users/profile/",
                related_booking_id=booking_id,
            )

            # In-app notification to mentor
            notifications.create_notification(
                recipient_id=mentor_id,
                event_type="session_reminder",
                title="Reminder: Upcoming Coaching Session",
                message=f"You have an upcoming coaching session with {student_name} on {date_str} at {time_str}.",
                link_url="/users/backoffice/mentors/sessions/",
                related_booking_id=booking_id,
            )

            # Email student
            student_email = session.get("student_email")
            if not student_email:
                s_user = db["users"].find_one({"_id": student_id})
                student_email = s_user.get("email") if s_user else None

            if student_email:
                email_service.send_templated_email(
                    subject=f"Session Reminder — Mentorship with {mentor_name}",
                    recipient_email=student_email,
                    template_name_base="session_reminder",
                    context={
                        "recipient_name": student_name,
                        "other_party_name": mentor_name,
                        "topic": topic,
                        "date": date_str,
                        "start_time": time_str,
                        "end_time": session.get("end_time", ""),
                        "duration_minutes": session.get("duration_minutes", 60),
                        "session_type": session.get("session_type", "online"),
                        "meeting_link": session.get("meeting_link", ""),
                        "location": session.get("location", ""),
                        "action_url": "/users/profile/",
                    },
                )

            # Mark reminder sent
            bookings_collection.update_one(
                {"_id": booking_id},
                {"$set": {"reminder_sent": True, "reminder_sent_at": now}},
            )
            reminded_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Successfully processed {reminded_count} reminders.")
        )
