from django.core.management.base import BaseCommand, CommandError
from pymongo.errors import PyMongoError

from apps.support.services import seed_default_faq


class Command(BaseCommand):
    help = "Charge les solutions par défaut dans la base de connaissances du support."

    def handle(self, *args, **options):
        try:
            created = seed_default_faq()
        except PyMongoError as error:
            raise CommandError("Impossible de charger la FAQ : MongoDB est indisponible.") from error
        if created:
            self.stdout.write(self.style.SUCCESS(f"{created} solutions chargées dans la base de connaissances."))
        else:
            self.stdout.write("La base de connaissances contient déjà des solutions.")
