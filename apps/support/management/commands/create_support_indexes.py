from django.core.management.base import BaseCommand, CommandError
from pymongo.errors import PyMongoError

from apps.support.repositories import create_indexes


class Command(BaseCommand):
    help = "Crée les index MongoDB du module support."

    def handle(self, *args, **options):
        try:
            create_indexes()
        except PyMongoError as error:
            raise CommandError("Impossible de créer les index support : MongoDB est indisponible.") from error
        self.stdout.write(self.style.SUCCESS("Index du support créés."))
