from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from pymongo import ASCENDING
from pymongo.errors import PyMongoError

from apps.users.services import USERS_COLLECTION, UserStorageError, ensure_users_indexes


class Command(BaseCommand):
    help = "Create required MongoDB indexes for the users collection."

    def handle(self, *args, **options):
        try:
            ensure_users_indexes(USERS_COLLECTION)
            from config.mongodb import db

            db[settings.MONGODB_SESSIONS_COLLECTION].create_index(
                [("expire_date", ASCENDING)], expireAfterSeconds=0
            )
        except (UserStorageError, PyMongoError) as error:
            raise CommandError("Could not prepare MongoDB indexes.") from error
        self.stdout.write(self.style.SUCCESS("MongoDB user and session indexes are ready."))
