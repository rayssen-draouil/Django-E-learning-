from django.core.management.base import BaseCommand, CommandError
from pymongo.errors import PyMongoError

from apps.courses.ai import get_ai_stats


class Command(BaseCommand):
    help = "Show AI assistant latency and failure metrics."

    def handle(self, *args, **options):
        try:
            stats = get_ai_stats()
        except PyMongoError as error:
            raise CommandError("Could not read AI metrics.") from error
        self.stdout.write(
            f"Appels: {stats['count']} | moyenne: {stats['average_ms']} ms | "
            f"p95: {stats['p95_ms']} ms | échecs: {stats['failure_rate']} %"
        )
