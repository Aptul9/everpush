from urllib.parse import parse_qs, urlsplit

import requests
from django.conf import settings

_config = {}

WRONG = "wrong"
DENIED = "denied"


def discovery():
    """EverID endpoints from the issuer's discovery document, cached after the first success."""
    if not _config:
        response = requests.get(settings.OIDC_ISSUER + ".well-known/openid-configuration", timeout=settings.OIDC_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if data.get("issuer") != settings.OIDC_ISSUER:
            raise ValueError("EverID discovery issuer mismatch")
        _config.update(data)
    return _config


def sign_in(username, password, authorize_url):
    """Signs in to EverID server-side with the app's own form and runs the app's authorization request.

    Returns the app callback URL (code and state) for the browser, WRONG for bad credentials or a stage the form
    cannot answer (MFA), DENIED when the credentials are right but EverID refuses this app. Each call uses its own
    cookie jar, so no EverID session outlives the request."""
    origin = settings.OIDC_ORIGIN
    timeout = settings.OIDC_TIMEOUT
    redirect_uri = parse_qs(urlsplit(authorize_url).query).get("redirect_uri", [""])[0]
    with requests.Session() as session:
        session.headers["Accept"] = "application/json"
        brand = session.get(origin + "/api/v3/core/brands/current/", timeout=timeout)
        brand.raise_for_status()
        flow = brand.json().get("flow_authentication") or "default-authentication-flow"
        executor = f"{origin}/api/v3/flows/executor/{flow}/?query="
        step = session.get(executor, timeout=timeout).json()
        identified = sent = False
        for _ in range(4):
            component = step.get("component")
            if component == "xak-flow-redirect":
                break
            if component == "ak-stage-identification" and not identified:
                identified = True
                payload = {"component": component, "uid_field": username, "password": password}
            elif component == "ak-stage-password" and not sent:
                sent = True
                payload = {"component": component, "password": password}
            else:
                return WRONG
            csrf = session.cookies.get("authentik_csrf", "")
            step = session.post(executor, json=payload, headers={"X-authentik-CSRF": csrf}, timeout=timeout).json()
        if step.get("component") != "xak-flow-redirect":
            return WRONG
        answer = session.get(authorize_url, allow_redirects=False, timeout=timeout)
        location = answer.headers.get("Location", "")
        if redirect_uri and location.startswith(redirect_uri + "?") and "code=" in location:
            return location
        return DENIED
