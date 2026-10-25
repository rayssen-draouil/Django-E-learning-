from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from pymongo import ASCENDING
from pymongo.errors import PyMongoError

<<<<<<< Updated upstream
from apps.users.services import USERS_COLLECTION, UserStorageError, ensure_users_indexes


class Command(BaseCommand):
    help = "Create required MongoDB indexes for the users collection."
=======
from apps.mentors.bookings import create_indexes as create_booking_indexes
from apps.mentors.notifications import create_indexes as create_notification_indexes
from apps.mentors.repositories import create_indexes as create_mentor_indexes
from apps.mentors.reviews import create_indexes as create_review_indexes
from apps.learning_paths.repositories import create_indexes as create_learning_path_indexes
from apps.users.services import (
    USERS_COLLECTION,
    UserStorageError,
    ensure_users_indexes,
)


class Command(BaseCommand):
    help = "Create required MongoDB indexes for users, sessions, mentors, bookings, reviews, notifications, and learning paths."
>>>>>>> Stashed changes

    def handle(self, *args, **options):
        try:
            ensure_users_indexes(USERS_COLLECTION)
            from config.mongodb import db

            db[settings.MONGODB_SESSIONS_COLLECTION].create_index(
                [("expire_date", ASCENDING)], expireAfterSeconds=0
            )
<<<<<<< Updated upstream
=======
            create_mentor_indexes()
            create_booking_indexes()
            create_review_indexes()
            create_notification_indexes()
            create_learning_path_indexes()
>>>>>>> Stashed changes
        except (UserStorageError, PyMongoError) as error:
            raise CommandError("Could not prepare MongoDB indexes.") from error
        self.stdout.write(self.style.SUCCESS("MongoDB user and session indexes are ready."))
