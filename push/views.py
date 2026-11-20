import logging
from functools import wraps

import requests

from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import SuspiciousOperation
from django.core.exceptions import RequestDataTooBig
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.http import urlencode
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
    return render(request, "login.html", {
        "error": error, "sso": settings.OIDC_ENABLED,
    }, status=status)


@ensure_csrf_cookie
def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method != "GET":
        return HttpResponse(status=405)
    if settings.OIDC_ENABLED and not request.COOKIES.get("sso_signed_out"):
        return redirect("/oidc/login/?silent=1")
    return login_page(request)


class EverIDCallbackView(OIDCAuthenticationCallbackView):
    def get(self, request):
        if not settings.OIDC_ENABLED:
            raise Http404
        if request.GET.get("error"):
            state = request.GET.get("state")
            if not state or state not in request.session.get("oidc_states", {}):
                raise SuspiciousOperation("Invalid OIDC callback state")
            request.silent_auth = request.session["oidc_states"][state].get("silent", False)
        return super().get(request)

    def login_success(self):
        response = super().login_success()
        response.delete_cookie("sso_signed_out")
        return response

    def login_failure(self):
        error = self.request.GET.get("error")
        if getattr(self.request, "everid_denied", False) or error == "access_denied":
            return login_page(self.request, "Il tuo account non ha accesso a EverPush.", 403)
        if getattr(self.request, "silent_auth", False) and error in {"login_required", "interaction_required", "consent_required", "account_selection_required"}:
            return redirect("/oidc/login/")
        return login_page(self.request, "Accesso non disponibile. Riprova.", 400)


@require_GET
def everid_login(request):
    if not settings.OIDC_ENABLED:
        raise Http404
    if request.user.is_authenticated:
        return redirect("home")
    return EverIDRequestView.as_view()(request)


class EverIDRequestView(OIDCAuthenticationRequestView):
    def get(self, request):
        try:
            self.OIDC_OP_AUTH_ENDPOINT = everid.discovery()["authorization_endpoint"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            LOGGER.warning("EverID discovery failed: %s", exc)
            return login_page(request, "Accesso non disponibile. Riprova.", 503)
        response = super().get(request)
        if request.GET.get("silent") == "1":
            from urllib.parse import parse_qs, urlsplit
            state = parse_qs(urlsplit(response["Location"]).query)["state"][0]
            request.session["oidc_states"][state]["silent"] = True
            request.session.modified = True
        return response

    def get_extra_params(self, request):
        return {"prompt": "none"} if request.GET.get("silent") == "1" else {}


def end_session_endpoint():
    try:
        return everid.discovery()["end_session_endpoint"]
    except (requests.RequestException, ValueError, KeyError):
        return settings.OIDC_OP_LOGOUT_ENDPOINT


@require_POST
def logout_view(request):
    id_token = request.session.get("oidc_id_token")
    logout(request)
    if settings.OIDC_ENABLED and id_token:
        query = urlencode({
            "id_token_hint": id_token,
            "post_logout_redirect_uri": request.build_absolute_uri("/"),
        })
        response = redirect(f"{end_session_endpoint()}?{query}")
    else:
        response = redirect("login")
    response.set_cookie("sso_signed_out", "1", max_age=86400, secure=not settings.INSECURE_LOCAL, httponly=True, samesite="Lax")
    return response


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
