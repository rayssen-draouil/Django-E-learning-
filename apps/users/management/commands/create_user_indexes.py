from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from pymongo import ASCENDING
from pymongo.errors import PyMongoError

from apps.mentors.bookings import create_indexes as create_booking_indexes
from apps.mentors.notifications import create_indexes as create_notification_indexes
from apps.mentors.repositories import create_indexes as create_mentor_indexes
from apps.mentors.reviews import create_indexes as create_review_indexes
from apps.users.services import (
    USERS_COLLECTION,
    UserStorageError,
    ensure_users_indexes,
)


class Command(BaseCommand):
    help = "Create required MongoDB indexes for users, sessions, mentors, bookings, reviews, and notifications."

    def handle(self, *args, **options):
        try:
            ensure_users_indexes(USERS_COLLECTION)
            from config.mongodb import db

            db[settings.MONGODB_SESSIONS_COLLECTION].create_index(
                [("expire_date", ASCENDING)], expireAfterSeconds=0
            )
            create_mentor_indexes()
            create_booking_indexes()
            create_review_indexes()
            create_notification_indexes()
        except (UserStorageError, PyMongoError) as error:
            raise CommandError("Could not prepare MongoDB indexes.") from error
        self.stdout.write(
            self.style.SUCCESS("MongoDB indexes are ready across all collections.")
        )
