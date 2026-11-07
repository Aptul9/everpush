import argparse
import hashlib
import http.cookiejar
import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class Browser:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ""

    def request(self, path, data=None, headers=None):
        values = {"Origin": self.base, "Referer": self.base + "/", **(headers or {})}
        if data is not None:
            values["X-CSRFToken"] = self.csrf
        request = urllib.request.Request(self.base + path, data=data, headers=values)
        try:
            response = self.opener.open(request, timeout=60)
        except urllib.error.HTTPError as response:
            return response.code, response.read()
        with response:
            return response.status, response.read()

    def page(self, path):
        status, body = self.request(path)
        assert status == 200, (status, body[:200])
        self.csrf = re.search(rb'name="csrf-token" content="([^"]+)"', body).group(1).decode()
        return body

    def post(self, path, fields=None, file=None):
        boundary = secrets.token_hex(16)
        parts = []
        for name, value in (fields or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        if file:
            name, content_type, data = file
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                         f'Content-Type: {content_type}\r\n\r\n'.encode() + data + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        status, raw = self.request(path, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return status, json.loads(raw)


def run(base, password):
    owner, guest, other = Browser(base), Browser(base), Browser(base)
    for attempt in range(30):
        try:
            owner.page("/login/")
            break
        except (OSError, AssertionError):
            if attempt == 29:
                raise
            time.sleep(1)
    form = urllib.parse.urlencode({"email": "aptul4ever@gmail.com", "password": password, "csrfmiddlewaretoken": owner.csrf}).encode()
    status, page = owner.request("/login/", form, {"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 200 and b"Il tuo <em>Push</em>" in page, (status, page[:200])
    owner.page("/")
    other.page("/login/")
    form = urllib.parse.urlencode({"email": "aptul4ever@gmail.com", "password": password, "csrfmiddlewaretoken": other.csrf}).encode()
    assert other.request("/login/", form, {"Content-Type": "application/x-www-form-urlencoded"})[0] == 200
    made = []
    try:
        start = json.loads(other.request("/api/items/")[1])["ids"]
        after = start[0] if start else 0
        note = "Smoke " + secrets.token_hex(8) + "\nseconda riga"
        status, text = owner.post("/api/items/", {"text": note})
        assert status == 201, (status, text)
        made.append(text["id"])
        payload = secrets.token_bytes(5 * 1024 * 1024 + 37)
        status, file = owner.post("/api/items/", file=("HTTP Smoke.bin", "application/octet-stream", payload))
        assert status == 201 and file["size"] == len(payload), (status, file)
        made.append(file["id"])
        status, body = other.request(f"/api/items/?after={after}")
        seen = json.loads(body)
        assert status == 200 and [item["id"] for item in seen["items"]][:2] == [file["id"], text["id"]], seen
        assert seen["items"][1]["text"] == note
        status, data = other.request(f"/api/items/{file['id']}/file/")
        assert status == 200 and hashlib.sha256(data).digest() == hashlib.sha256(payload).digest(), status
        status, large = owner.post("/api/items/", file=("Grande.bin", "application/octet-stream", b"x" * (10 * 1024 * 1024 + 1)))
        assert status == 413 and large["drive"] == "https://drive.everbright.it", (status, large)
        assert guest.request("/api/items/")[0] == 401
        assert guest.request(f"/api/items/{file['id']}/file/")[0] == 401
    finally:
        for item_id in made:
            status, _ = owner.request(f"/api/items/{item_id}/delete/", b"")
            assert status == 200, status
    remaining = json.loads(owner.request("/api/items/")[1])["ids"]
    assert not set(made) & set(remaining), remaining
    print("HTTP smoke: PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    run(parser.parse_args().url, sys.stdin.readline().rstrip("\r\n"))
