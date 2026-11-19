from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import RequestDataTooBig
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.http import urlencode
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView, OIDCAuthenticationRequestView

from .models import Item
from . import service


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
        "error": error, "everid": settings.OIDC_ENABLED, "legacy_login": settings.LEGACY_LOGIN,
    }, status=status)


@ensure_csrf_cookie
def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    error = None
    status = 200
    if request.method == "POST":
        if not settings.LEGACY_LOGIN:
            return login_page(request, "Accesso con password disattivato. Usa EverID.", 403)
        try:
            service.rate_limit(request, "login", 10, 15 * 60)
            user = authenticate(request, username=request.POST.get("email", ""), password=request.POST.get("password", ""))
            if user:
                login(request, user)
                return redirect("home")
            error = "Email o password non corretti."
            status = 400
        except service.PushError as exc:
            error, status = exc.message, exc.status
    elif request.method != "GET":
        return HttpResponse(status=405)
    elif request.GET.get("everid") == "error":
        error = "Accesso con EverID non riuscito. Riprova."
    return login_page(request, error, status)


class EverIDCallbackView(OIDCAuthenticationCallbackView):
    def get(self, request):
        if not settings.OIDC_ENABLED:
            raise Http404
        return super().get(request)

    def login_failure(self):
        if getattr(self.request, "everid_denied", False):
            return login_page(self.request, "Il tuo account EverID non ha accesso a EverPush.", 403)
        return redirect("/login/?everid=error")


@require_GET
def everid_login(request):
    if not settings.OIDC_ENABLED:
        raise Http404
    if request.user.is_authenticated:
        return redirect("home")
    return OIDCAuthenticationRequestView.as_view()(request)


@require_POST
def logout_view(request):
    id_token = request.session.get("oidc_id_token")
    logout(request)
    if settings.OIDC_ENABLED and id_token:
        query = urlencode({
            "id_token_hint": id_token,
            "post_logout_redirect_uri": request.build_absolute_uri("/login/"),
        })
        return redirect(f"{settings.OIDC_OP_LOGOUT_ENDPOINT}?{query}")
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
