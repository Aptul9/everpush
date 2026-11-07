import hashlib
import hmac
import os
import re
import shutil
import unicodedata
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Item, RateLimit

INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$")


class PushError(Exception):
    def __init__(self, message, status=400, **extra):
        self.message = message
        self.status = status
        self.extra = extra


def too_large():
    limit = settings.MAX_FILE_BYTES // (1024 * 1024)
    return PushError(f"Il file supera {limit} MB. Per i file grandi usa EverDrive.", 413, drive=settings.DRIVE_URL)


def clean_name(name):
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if not unicodedata.category(c).startswith("C")).strip()
    if not name or name in (".", "..") or len(name) > 240:
        raise PushError("Nome file non valido o troppo lungo.")
    return name


def clean_type(value):
    value = (value or "").split(";")[0].strip().lower()
    return value if CONTENT_TYPE.match(value) else "application/octet-stream"


def path_for(item):
    return settings.FILE_DIR / item.stored_name


def disk_free():
    return shutil.disk_usage(settings.DATA_DIR).free


def rate_limit(request, purpose, limit, seconds):
    address = request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "unknown")
    key = hmac.new(settings.SECRET_KEY.encode(), f"{purpose}:{address}".encode(), hashlib.sha256).hexdigest()
    now = timezone.now()
    with transaction.atomic():
        row, _ = RateLimit.objects.get_or_create(key=key, defaults={"expires_at": now + timedelta(seconds=seconds)})
        if row.expires_at <= now:
            row.count = 0
            row.expires_at = now + timedelta(seconds=seconds)
        if row.count >= limit:
            raise PushError("Troppi tentativi. Riprova più tardi.", 429)
        row.count += 1
        row.save()


def trim():
    # File bytes are removed only after the row deletion commits.
    old = list(Item.objects.order_by("-id").values_list("id", "stored_name")[settings.MAX_ITEMS:])
    if not old:
        return
    Item.objects.filter(id__in=[item_id for item_id, _ in old]).delete()
    names = [name for _, name in old if name]
    transaction.on_commit(lambda: [(settings.FILE_DIR / name).unlink(missing_ok=True) for name in names])


def add_text(text):
    if not isinstance(text, str) or not text.strip():
        raise PushError("Scrivi qualcosa da inviare.")
    # Browsers submit form text with CRLF line endings.
    text = text.replace("\r\n", "\n")
    if len(text) > settings.MAX_TEXT_CHARS:
        raise PushError(f"Testo troppo lungo: massimo {settings.MAX_TEXT_CHARS:,} caratteri.".replace(",", "."), 413)
    with transaction.atomic():
        item = Item.objects.create(text=text)
        trim()
    return item


def add_file(upload):
    if upload.size > settings.MAX_FILE_BYTES:
        raise too_large()
    if disk_free() - upload.size < settings.RESERVE_BYTES:
        raise PushError("Spazio sul server esaurito.", 507)
    name = clean_name(upload.name)
    settings.FILE_DIR.mkdir(parents=True, exist_ok=True)
    stored = uuid.uuid4().hex
    target = settings.FILE_DIR / stored
    partial = target.with_suffix(".part")
    written = 0
    with partial.open("wb") as output:
        for part in upload.chunks():
            written += len(part)
            if written > settings.MAX_FILE_BYTES:
                break
            output.write(part)
    if written > settings.MAX_FILE_BYTES:
        partial.unlink(missing_ok=True)
        raise too_large()
    os.replace(partial, target)
    try:
        with transaction.atomic():
            item = Item.objects.create(file_name=name, file_size=written, stored_name=stored,
                                       content_type=clean_type(upload.content_type))
            trim()
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return item


def delete(item_id):
    with transaction.atomic():
        item = Item.objects.filter(id=item_id).first()
        if not item:
            raise PushError("Elemento non trovato.", 404)
        item.delete()
        if item.stored_name:
            transaction.on_commit(lambda: path_for(item).unlink(missing_ok=True))


def remove_orphans():
    if not settings.FILE_DIR.is_dir():
        return 0
    # Recent files may belong to an upload still committing in the running release.
    cutoff = timezone.now().timestamp() - 600
    known = set(Item.objects.exclude(stored_name="").values_list("stored_name", flat=True))
    removed = 0
    for path in settings.FILE_DIR.iterdir():
        if path.is_file() and path.name not in known and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def serialize(item):
    value = {"id": item.id, "created_at": item.created_at.isoformat()}
    if item.stored_name:
        value.update(kind="file", name=item.file_name, size=item.file_size,
                     image=item.content_type in INLINE_TYPES)
    else:
        value.update(kind="text", text=item.text)
    return value
