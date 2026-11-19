import requests
from django.conf import settings

_config = {}


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
