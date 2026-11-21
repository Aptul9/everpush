import os
import secrets
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from .everid import discovery as real_discovery
from . import everid
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
        self.assertContains(page, 'type="password"')

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
        self.assertEqual(self.guest.put("/login/").status_code, 405)

    def test_bootstrap_owner_never_resets_password(self):
        with patch("sys.stdin.readline", return_value="another-password-123\n"):
            call_command("bootstrap_owner", stdout=open(os.devnull, "w"))
        self.assertIsNotNone(authenticate(username=settings.OWNER_EMAIL, password=self.password))


PASSWORD = secrets.token_urlsafe(24)
EVERID = dict(
    OIDC_ENABLED=True, OIDC_RP_CLIENT_ID="everpush-client", OIDC_RP_CLIENT_SECRET="everpush-secret",
    OIDC_ISSUER="https://id.example.test/application/o/everpush/", OIDC_ORIGIN="https://id.example.test",
    OIDC_OP_AUTHORIZATION_ENDPOINT="https://id.example.test/application/o/authorize/",
    OIDC_OP_LOGOUT_ENDPOINT="https://id.example.test/application/o/everpush/end-session/",
    ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
)


DISCOVERY = {
    "issuer": EVERID["OIDC_ISSUER"],
    "authorization_endpoint": "https://id.example.test/application/o/authorize/",
    "token" + "_endpoint": "https://id.example.test/application/o/" + "token/",
    "userinfo_endpoint": "https://id.example.test/application/o/userinfo/",
    "jwks_uri": "https://id.example.test/application/o/everpush/jwks/",
    "end_session_endpoint": "https://id.example.test/application/o/everpush/end-session/",
}


@override_settings(**EVERID)
class EverIDTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(settings.OWNER_EMAIL, settings.OWNER_EMAIL, PASSWORD)
        self.client = Client()
        discovery = patch("push.everid.discovery", return_value=DISCOVERY)
        discovery.start()
        self.addCleanup(discovery.stop)

    def form_login(self, username, result=None):
        """POST the app's own form; EverID is replaced by a stub that answers like the real sign-in."""
        seen = {}

        def fake_sign_in(user, password, authorize_url):
            seen.update(user=user, password=password, url=authorize_url)
            if result is not None:
                return result
            query = parse_qs(urlsplit(authorize_url).query)
            return query["redirect_uri"][0] + "?" + urlencode({"code": "code", "state": query["state"][0]})

        with patch("push.everid.sign_in", side_effect=fake_sign_in):
            response = self.client.post("/login/", {"email": username, "password": "the-password"})
        return response, seen

    def everid_login(self, email, groups, iss=EVERID["OIDC_ISSUER"], aud="everpush-client"):
        start, seen = self.form_login(email)
        self.assertEqual(start.status_code, 302)
        self.assertEqual(seen["password"], "the-password")
        self.assertTrue(seen["url"].startswith("https://id.example.test/application/o/authorize/?"))
        query = parse_qs(urlsplit(seen["url"]).query)
        self.assertEqual(query["redirect_uri"], ["http://testserver/oidc/callback"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["scope"], ["openid email profile"])
        self.assertNotIn("prompt", query)
        callback = urlsplit(start["Location"])
        self.assertEqual(callback.path, "/oidc/callback")
        id_token = {"iss": iss, "aud": aud, "nonce": query["nonce"][0], "email": email}
        claims = {"email": email, "groups": groups}
        base = "mozilla_django_oidc.auth.OIDCAuthenticationBackend."
        with patch(base + "get_token", return_value={"id_token": "id-token", "access_token": "access"}), \
                patch(base + "verify_token", return_value=id_token), \
                patch(base + "get_userinfo", return_value=claims):
            return self.client.get("/oidc/callback", dict(parse_qs(callback.query)))

    def test_group_allowed_matches_existing_owner_by_email(self):
        response = self.everid_login(settings.OWNER_EMAIL.upper(), ["app-push", "everid-admins"])
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.owner.pk)
        self.assertEqual(self.client.get("/api/items/").status_code, 200)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_new_user_created_and_sees_shared_items(self):
        Item.objects.create(text="Owner note")
        response = self.everid_login("newcomer@example.com", ["app-push"])
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(email="newcomer@example.com")
        self.assertFalse(user.has_usable_password())
        texts = [i["text"] for i in self.client.get("/api/items/").json()["items"]]
        self.assertEqual(texts, ["Owner note"])

    def test_group_denied_403_and_no_session(self):
        response = self.everid_login("outsider@example.com", ["app-drive"])
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "non ha accesso a EverPush", status_code=403)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(get_user_model().objects.filter(email="outsider@example.com").exists())
        self.assertEqual(self.client.get("/api/items/").status_code, 401)

    def test_wrong_issuer_or_audience_rejected(self):
        for kwargs in ({"iss": "https://evil.test/"}, {"aud": "other-client"}):
            self.client = Client()
            response = self.everid_login(settings.OWNER_EMAIL, ["app-push"], **kwargs)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_password_generic_message(self):
        response, _ = self.form_login(settings.OWNER_EMAIL, everid.WRONG)
        self.assertContains(response, "Email o password non corretti.", status_code=400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_everid_refuses_app_403(self):
        response, _ = self.form_login("outsider@example.com", everid.DENIED)
        self.assertContains(response, "non ha accesso a EverPush", status_code=403)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_rate_limited(self):
        for _ in range(10):
            self.form_login("x", everid.WRONG)
        response, seen = self.form_login("x", everid.WRONG)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(seen, {})

    def test_csrf_required_on_form(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post("/login/", {"email": "a", "password": "b"}).status_code, 403)

    def test_logout_is_local_and_shows_the_form(self):
        self.everid_login(settings.OWNER_EMAIL, ["app-push"])
        response = self.client.post("/logout/")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/api/items/").status_code, 401)
        self.assertContains(self.client.get("/login/"), 'data-testid="login-password"')

    def test_discovery_failure_fails_closed_to_login_page(self):
        with patch("push.everid.discovery", side_effect=ValueError("down")):
            response = self.client.post("/login/", {"email": "a", "password": "b"})
        self.assertEqual(response.status_code, 503)

    def test_discovery_rejects_foreign_issuer(self):
        response = Mock(json=lambda: {**DISCOVERY, "issuer": "https://evil.test/"})
        with patch("push.everid._config", {}), patch("push.everid.requests.get", return_value=response):
            with self.assertRaises(ValueError):
                real_discovery()

    def test_login_page_is_the_apps_own_form(self):
        response = self.client.get("/login/")
        page = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-testid="login-password"', page)
        self.assertNotIn("EverID", page)
        self.assertNotIn("id.example.test", page)
        self.assertNotIn("/oidc/login/", page)

    @override_settings(LEGACY_LOGIN=False)
    def test_local_password_never_checked(self):
        self.assertIsNone(authenticate(username=settings.OWNER_EMAIL, password=PASSWORD))
        response, _ = self.form_login(settings.OWNER_EMAIL, everid.WRONG)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_legacy_session_dropped_when_legacy_turned_off(self):
        self.client.force_login(self.owner, backend="push.auth.OwnerBackend")
        self.assertEqual(self.client.get("/api/items/").status_code, 200)
        with override_settings(LEGACY_LOGIN=False):
            self.assertEqual(self.client.get("/api/items/").status_code, 401)

    @override_settings(OIDC_ENABLED=False)
    def test_everid_routes_absent_when_not_configured(self):
        self.assertEqual(self.client.post("/login/", {"email": "a", "password": "b"}).status_code, 503)
        self.assertEqual(self.client.get("/oidc/callback", {"code": "x", "state": "y"}).status_code, 404)


class SignInTests(TestCase):
    """everid.sign_in against a fake EverID: stages, wrong password, MFA, refused app."""

    AUTHORIZE = "https://id.example.test/application/o/authorize/?" + urlencode(
        {"client_id": "everpush-client", "redirect_uri": "https://push.test/oidc/callback", "state": "s1"})

    def run_flow(self, steps, authorize_location):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.headers = {}
        session.cookies = {"authentik_csrf": "c"}
        brand = Mock(json=lambda: {"flow_authentication": "accesso"})
        first, *rest = steps
        session.get.side_effect = [brand, Mock(json=lambda: first), Mock(headers={"Location": authorize_location})]
        session.post.side_effect = [Mock(json=(lambda s=s: s)) for s in rest]
        with override_settings(**EVERID), patch("push.everid.requests.Session", return_value=session):
            return everid.sign_in("aptul", "pw", self.AUTHORIZE), session

    def test_success_returns_app_callback(self):
        callback = "https://push.test/oidc/callback?code=abc&state=s1"
        result, session = self.run_flow([{"component": "ak-stage-identification"}, {"component": "ak-stage-password"},
                                         {"component": "xak-flow-redirect"}], callback)
        self.assertEqual(result, callback)
        self.assertEqual(session.post.call_args_list[0].kwargs["json"]["uid_field"], "aptul")
        self.assertEqual(session.post.call_args_list[1].kwargs["headers"], {"X-authentik-CSRF": "c"})

    def test_wrong_password(self):
        result, _ = self.run_flow([{"component": "ak-stage-identification"}, {"component": "ak-stage-password"},
                                   {"component": "ak-stage-password", "response_errors": {"password": ["x"]}}], "")
        self.assertEqual(result, everid.WRONG)

    def test_mfa_stage_refused(self):
        result, _ = self.run_flow([{"component": "ak-stage-identification"},
                                   {"component": "ak-stage-authenticator-validate"}], "")
        self.assertEqual(result, everid.WRONG)

    def test_app_refused_after_good_credentials(self):
        result, _ = self.run_flow([{"component": "ak-stage-identification"}, {"component": "xak-flow-redirect"}],
                                  "/if/flow/accesso/?denied")
        self.assertEqual(result, everid.DENIED)
