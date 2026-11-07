from django.core.management.base import BaseCommand
from push.service import remove_orphans


class Command(BaseCommand):
    help = "Remove stored files that no item references, such as uploads interrupted by a restart."

    def handle(self, *args, **options):
        self.stdout.write(f"Orphan files removed: {remove_orphans()}")
