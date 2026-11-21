import logging
from functools import wraps

import requests

from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import RequestDataTooBig
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView, OIDCAuthenticationRequestView

from .models import Item
from . import everid, service

LOGGER = logging.getLogger(__name__)


def api(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Sessione scaduta. Accedi di nuovo."}, status=401)
        try:
            return view(request, *args, **kwargs)
        except service.PushError as exc:
            return JsonResponse({"error": exc.message, **exc.extra}, status=exc.status)
        except RequestDataTooBig:
            return JsonResponse({"error": "Testo troppo lungo."}, status=413)
        except (ValueError, TypeError):
            return JsonResponse({"error": "Richiesta non valida."}, status=400)
    return wrapped


@ensure_csrf_cookie
@require_GET
def home(request):
    if not request.user.is_authenticated:
        return redirect("login")
    return render(request, "push.html", {
        "max_items": settings.MAX_ITEMS, "max_file_bytes": settings.MAX_FILE_BYTES,
        "max_text_chars": settings.MAX_TEXT_CHARS, "drive_url": settings.DRIVE_URL,
    })


def login_page(request, error=None, status=200):
    return render(request, "login.html", {"error": error}, status=status)


WRONG_CREDENTIALS = "Email o password non corretti."
UNAVAILABLE = "Accesso non disponibile. Riprova."


@ensure_csrf_cookie
def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "GET":
        return login_page(request)
    if request.method != "POST":
        return HttpResponse(status=405)
    if not settings.OIDC_ENABLED:
        return login_page(request, UNAVAILABLE, 503)
    try:
        service.rate_limit(request, "login", 10, 15 * 60)
    except service.PushError as exc:
        return login_page(request, exc.message, exc.status)
    username = request.POST.get("email", "").strip()[:254]
    password = request.POST.get("password", "")[:1024]
    if not username or not password:
        return login_page(request, WRONG_CREDENTIALS, 400)
    view = EverIDRequestView()
    view.setup(request)
    start = view.get(request)
    if start.status_code != 302:
        return start
    try:
        result = everid.sign_in(username, password, start["Location"])
    except (requests.RequestException, ValueError) as exc:
        LOGGER.warning("Sign-in through EverID failed: %s", type(exc).__name__)
        return login_page(request, UNAVAILABLE, 503)
    if result == everid.WRONG:
        return login_page(request, WRONG_CREDENTIALS, 400)
    if result == everid.DENIED:
        return login_page(request, "Il tuo account non ha accesso a EverPush.", 403)
    return redirect(result)


class EverIDCallbackView(OIDCAuthenticationCallbackView):
    def get(self, request):
        if not settings.OIDC_ENABLED:
            raise Http404
        return super().get(request)

    def login_failure(self):
        if getattr(self.request, "everid_denied", False) or self.request.GET.get("error") == "access_denied":
            return login_page(self.request, "Il tuo account non ha accesso a EverPush.", 403)
        return login_page(self.request, UNAVAILABLE, 400)


class EverIDRequestView(OIDCAuthenticationRequestView):
    def get(self, request):
        try:
            self.OIDC_OP_AUTH_ENDPOINT = everid.discovery()["authorization_endpoint"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            LOGGER.warning("EverID discovery failed: %s", exc)
            return login_page(request, UNAVAILABLE, 503)
        return super().get(request)


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


@require_GET
def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


@require_http_methods(["GET", "POST"])
@api
def items(request):
    if request.method == "POST":
        upload = request.FILES.get("file")
        item = service.add_file(upload) if upload else service.add_text(request.POST.get("text"))
        return JsonResponse(service.serialize(item), status=201)
    after = int(request.GET.get("after") or 0)
    ids = list(Item.objects.order_by("-id").values_list("id", flat=True))
    rows = Item.objects.filter(id__gt=after).order_by("-id")
    return JsonResponse({"ids": ids, "items": [service.serialize(item) for item in rows]})


@require_POST
@api
def delete(request, item_id):
    service.delete(item_id)
    return JsonResponse({"deleted": True})


@require_GET
@api
def file(request, item_id):
    item = Item.objects.filter(id=item_id).exclude(stored_name="").first()
    if not item:
        raise Http404
    try:
        stream = service.path_for(item).open("rb")
    except FileNotFoundError:
        raise Http404
    inline = request.GET.get("inline") == "1" and item.content_type in service.INLINE_TYPES
    response = FileResponse(stream, as_attachment=not inline, filename=item.file_name,
                            content_type=item.content_type if inline else "application/octet-stream")
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response
