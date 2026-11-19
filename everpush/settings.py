import os
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False
ALLOWED_HOSTS = os.getenv("PUSH_ALLOWED_HOSTS", "push.everbright.it").split(",")
OWNER_EMAIL = "aptul4ever@gmail.com"
DATA_DIR = Path(os.getenv("PUSH_DATA_DIR", "/data"))
FILE_DIR = DATA_DIR / "files"
RESERVE_BYTES = int(os.getenv("PUSH_RESERVE_BYTES", "20000000000"))
MAX_ITEMS = 300
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
DRIVE_URL = "https://drive.everbright.it"
if RESERVE_BYTES < 20_000_000_000:
    raise ValueError("VM reserve must be at least 20 GB")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "push",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "push.middleware.PrivateResponsesMiddleware",
]
ROOT_URLCONF = "everpush.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
    ]},
}]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": DATA_DIR / "push.sqlite3",
    "OPTIONS": {"timeout": 30, "transaction_mode": "IMMEDIATE"},
}}
# EverID (Authentik) OIDC. Endpoints follow Authentik's layout under the per-application issuer.
OIDC_ISSUER = os.getenv("OIDC_ISSUER", "https://id.everbright.it/application/o/everpush/").rstrip("/") + "/"
_issuer = urlsplit(OIDC_ISSUER)
OIDC_ORIGIN = f"{_issuer.scheme}://{_issuer.netloc}"
OIDC_RP_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "")
OIDC_RP_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET", "")
OIDC_ENABLED = bool(OIDC_RP_CLIENT_ID and OIDC_RP_CLIENT_SECRET)
OIDC_REQUIRED_GROUP = "app-push"
OIDC_OP_AUTHORIZATION_ENDPOINT = OIDC_ORIGIN + "/application/o/authorize/"
OIDC_OP_TOKEN_ENDPOINT = OIDC_ORIGIN + "/application/o/token/"
OIDC_OP_USER_ENDPOINT = OIDC_ORIGIN + "/application/o/userinfo/"
OIDC_OP_JWKS_ENDPOINT = OIDC_ISSUER + "jwks/"
OIDC_OP_LOGOUT_ENDPOINT = OIDC_ISSUER + "end-session/"
OIDC_RP_SIGN_ALGO = "RS256"
OIDC_RP_SCOPES = "openid email profile"
OIDC_USE_PKCE = True
OIDC_STORE_ID_TOKEN = True
OIDC_TIMEOUT = 10
OIDC_AUTHENTICATION_CALLBACK_URL = "oidc_authentication_callback"
LOGIN_REDIRECT_URL = "/"
LOGIN_REDIRECT_URL_FAILURE = "/login/"
# Password login is the fallback until EverID passes live E2E.
LEGACY_LOGIN = os.getenv("LEGACY_LOGIN", "1") == "1"
if not (LEGACY_LOGIN or OIDC_ENABLED):
    raise ValueError("Password login disabled and EverID not configured: nobody could log in")
AUTHENTICATION_BACKENDS = ["push.auth.EverIDBackend", "push.auth.OwnerBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LANGUAGE_CODE = "it-it"
TIME_ZONE = "Europe/Rome"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
# Text is counted in characters; four bytes per character covers any UTF-8 input.
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_TEXT_CHARS * 4 + 64 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_FILE_BYTES + 1
SESSION_COOKIE_AGE = 30 * 24 * 60 * 60
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
INSECURE_LOCAL = os.getenv("PUSH_INSECURE_LOCAL") == "1"
SESSION_COOKIE_SECURE = not INSECURE_LOCAL
CSRF_COOKIE_SECURE = not INSECURE_LOCAL
SESSION_COOKIE_NAME = "push_session" if INSECURE_LOCAL else "__Host-push_session"
CSRF_COOKIE_NAME = "push_csrf" if INSECURE_LOCAL else "__Host-push_csrf"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not INSECURE_LOCAL
SECURE_HSTS_SECONDS = 31536000 if not INSECURE_LOCAL else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# Browser preload enrollment is a separate domain operation.
SILENCED_SYSTEM_CHECKS = ["security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
LOGIN_URL = "/login/"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
