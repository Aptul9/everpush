import sys
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Create the owner account once, reading its initial password from stdin."

    def handle(self, *args, **options):
        settings.FILE_DIR.mkdir(parents=True, exist_ok=True)
        User = get_user_model()
        with transaction.atomic():
            if User.objects.filter(username=settings.OWNER_EMAIL).exists():
                self.stdout.write("Owner already exists; password unchanged")
                return
            password = sys.stdin.readline().rstrip("\r\n")
            if not password:
                raise CommandError("Initial owner password must be supplied on stdin")
            validate_password(password)
            User.objects.create_user(username=settings.OWNER_EMAIL, email=settings.OWNER_EMAIL, password=password)
        self.stdout.write("Owner created")
