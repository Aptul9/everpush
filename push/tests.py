import os
import secrets
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from .models import Item


class PushTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.override = override_settings(
            DATA_DIR=root, FILE_DIR=root / "files", MAX_ITEMS=5,
            ALLOWED_HOSTS=["testserver", "localhost"], INSECURE_LOCAL=True,
            SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.free = patch("push.service.disk_free", return_value=settings.RESERVE_BYTES + 1024 ** 3)
        self.free.start()
        self.addCleanup(self.free.stop)
        self.password = secrets.token_urlsafe(24)
        self.user = get_user_model().objects.create_user(settings.OWNER_EMAIL, settings.OWNER_EMAIL, self.password)
        self.owner = Client()
        self.owner.force_login(self.user)
        self.guest = Client()

    def text(self, value, client=None):
        return (client or self.owner).post("/api/items/", {"text": value})

    def file(self, data=b"hello push", name="Nota.txt", content_type="text/plain", client=None):
        return (client or self.owner).post("/api/items/", {"file": SimpleUploadedFile(name, data, content_type)})

    def download(self, item, query=""):
        response = self.owner.get(f"/api/items/{item['id']}/file/{query}")
        self.addCleanup(response.close)
        return response

    def test_owner_auth_only(self):
        self.assertIsNotNone(authenticate(username=settings.OWNER_EMAIL, password=self.password))
        get_user_model().objects.create_user("other@example.com", password=self.password)
        self.assertIsNone(authenticate(username="other@example.com", password=self.password))

    def test_guest_has_no_access(self):
        item = self.file().json()
        self.assertEqual(self.guest.get("/").status_code, 302)
        self.assertEqual(self.guest.get("/api/items/").status_code, 401)
        self.assertEqual(self.text("x", self.guest).status_code, 401)
        self.assertEqual(self.guest.get(f"/api/items/{item['id']}/file/").status_code, 401)
        self.assertEqual(self.guest.post(f"/api/items/{item['id']}/delete/").status_code, 401)

    def test_login_page_and_form(self):
        client = Client(enforce_csrf_checks=True)
        page = client.get("/login/")
        self.assertContains(page, "Accedi a EverPush")
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.post("/login/", {"email": settings.OWNER_EMAIL.upper(), "password": self.password, "csrfmiddlewaretoken": token})
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertContains(client.get("/"), "Il tuo <em>Push</em>")

    def test_text_round_trip_and_incremental_sync(self):
        first = self.text("primo\r\n  riga due").json()
        self.assertEqual(first["text"], "primo\n  riga due")
        second = self.text("secondo").json()
        value = self.owner.get(f"/api/items/?after={first['id']}").json()
        self.assertEqual(value["ids"], [second["id"], first["id"]])
        self.assertEqual([item["id"] for item in value["items"]], [second["id"]])

    def test_empty_and_long_text_rejected(self):
        self.assertEqual(self.text("   ").status_code, 400)
        self.assertEqual(self.text("x" * (settings.MAX_TEXT_CHARS + 1)).status_code, 413)
        self.assertEqual(self.text("x" * settings.MAX_TEXT_CHARS).status_code, 201)

    def test_file_download_is_attachment(self):
        item = self.file(b"<script>alert(1)</script>", "pagina.html", "text/html").json()
        self.assertEqual(item["size"], 25)
        self.assertFalse(item["image"])
        response = self.download(item, "?inline=1")
        self.assertEqual(b"".join(response.streaming_content), b"<script>alert(1)</script>")
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        self.assertIn("sandbox", response["Content-Security-Policy"])

    def test_image_inline_only_when_requested(self):
        item = self.file(b"\x89PNG\r\n\x1a\n", "schermata.png", "image/png").json()
        self.assertTrue(item["image"])
        inline = self.download(item, "?inline=1")
        self.assertEqual(inline["Content-Type"], "image/png")
        self.assertTrue(inline["Content-Disposition"].startswith("inline"))
        self.assertTrue(self.download(item)["Content-Disposition"].startswith("attachment"))

    def test_file_over_limit_points_to_drive(self):
        response = self.file(b"x" * (settings.MAX_FILE_BYTES + 1), "grande.bin")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["drive"], "https://drive.everbright.it")
        self.assertEqual(Item.objects.count(), 0)
        self.assertEqual(list(settings.FILE_DIR.glob("*")), [])
        self.assertEqual(self.file(b"x" * settings.MAX_FILE_BYTES, "giusto.bin").status_code, 201)

    def test_reserve_protected(self):
        with patch("push.service.disk_free", return_value=settings.RESERVE_BYTES + 5):
            self.assertEqual(self.file(b"0123456789").status_code, 507)
        self.assertEqual(Item.objects.count(), 0)

    def test_oldest_items_and_files_removed_beyond_limit(self):
        oldest = self.file(b"vecchio", "vecchio.txt").json()
        stored = Item.objects.get(id=oldest["id"]).stored_name
        with self.captureOnCommitCallbacks(execute=True):
            for number in range(settings.MAX_ITEMS):
                self.text(f"testo {number}")
        self.assertEqual(Item.objects.count(), settings.MAX_ITEMS)
        self.assertFalse(Item.objects.filter(id=oldest["id"]).exists())
        self.assertFalse((settings.FILE_DIR / stored).exists())
        ids = self.owner.get("/api/items/").json()["ids"]
        self.assertEqual(len(ids), settings.MAX_ITEMS)
        self.assertNotIn(oldest["id"], ids)

    def test_delete_removes_file(self):
        item = self.file().json()
        stored = Item.objects.get(id=item["id"]).stored_name
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.owner.post(f"/api/items/{item['id']}/delete/").status_code, 200)
        self.assertFalse((settings.FILE_DIR / stored).exists())
        self.assertEqual(self.owner.post(f"/api/items/{item['id']}/delete/").status_code, 404)

    def test_file_name_sanitized(self):
        item = self.file(name="..\\..\\etc\\pass\x00wd").json()
        self.assertEqual(item["name"], "passwd")

    def test_cleanup_removes_only_old_orphans(self):
        item = self.file().json()
        stored = Item.objects.get(id=item["id"]).stored_name
        orphan = settings.FILE_DIR / "orphan"
        recent = settings.FILE_DIR / "recent"
        orphan.write_bytes(b"x")
        recent.write_bytes(b"x")
        os.utime(orphan, (0, 0))
        call_command("cleanup", stdout=open(os.devnull, "w"))
        self.assertFalse(orphan.exists())
        self.assertTrue(recent.exists())
        self.assertTrue((settings.FILE_DIR / stored).exists())

    def test_login_rate_limited(self):
        for _ in range(10):
            self.guest.post("/login/", {"email": settings.OWNER_EMAIL, "password": "wrong"})
        response = self.guest.post("/login/", {"email": settings.OWNER_EMAIL, "password": self.password})
        self.assertEqual(response.status_code, 429)

    def test_bootstrap_owner_never_resets_password(self):
        with patch("sys.stdin.readline", return_value="another-password-123\n"):
            call_command("bootstrap_owner", stdout=open(os.devnull, "w"))
        self.assertIsNotNone(authenticate(username=settings.OWNER_EMAIL, password=self.password))
