from django.core.management.base import BaseCommand, CommandError
from pymongo.errors import PyMongoError

from apps.courses import repository


class Command(BaseCommand):
    help = "Create required MongoDB indexes for the courses collection."

    def handle(self, *args, **options):
        try:
            repository.create_indexes()
        except PyMongoError as error:
            raise CommandError("Could not prepare MongoDB course indexes.") from error
        self.stdout.write(self.style.SUCCESS("MongoDB course indexes are ready."))
