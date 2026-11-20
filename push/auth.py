import logging

import requests
from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from . import everid

LOGGER = logging.getLogger(__name__)
ENDPOINTS = {
    "OIDC_OP_" + "TOKEN_ENDPOINT": "token" + "_endpoint",
    "OIDC_OP_USER_ENDPOINT": "userinfo_endpoint",
    "OIDC_OP_JWKS_ENDPOINT": "jwks_uri",
}


class OwnerBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not settings.LEGACY_LOGIN or password is None:
            return None
        email = (username or "").strip().lower()
        if email != settings.OWNER_EMAIL:
            from django.contrib.auth.hashers import make_password
            make_password(password or "")
            return None
        return super().authenticate(request, username=email, password=password, **kwargs)

    def get_user(self, user_id):
        if not settings.LEGACY_LOGIN:
            return None
        user = super().get_user(user_id)
        return user if user and user.username == settings.OWNER_EMAIL else None


class EverIDBackend(OIDCAuthenticationBackend):
    """EverID login: users matched or created by email, access only with the app group."""

    def authenticate(self, request, **kwargs):
        if not settings.OIDC_ENABLED or "password" in kwargs:
            return None
        try:
            config = everid.discovery()
            for attribute, key in ENDPOINTS.items():
                setattr(self, attribute, config[key])
        except (requests.RequestException, ValueError, KeyError) as exc:
            LOGGER.warning("EverID discovery failed: %s", exc)
            return None
        return super().authenticate(request, **kwargs)

    def verify_token(self, token, **kwargs):
        payload = super().verify_token(token, **kwargs)
        audience = payload.get("aud")
        audience = audience if isinstance(audience, list) else [audience]
        if payload.get("iss") != settings.OIDC_ISSUER or settings.OIDC_RP_CLIENT_ID not in audience:
            LOGGER.warning("EverID ID token rejected: issuer or audience mismatch")
            return None
        return payload

    def verify_claims(self, claims):
        if not claims.get("email"):
            return False
        groups = claims.get("groups") or []
        if settings.OIDC_REQUIRED_GROUP not in groups:
            self.request.everid_denied = True
            return False
        return True

    def filter_users_by_claims(self, claims):
        return self.UserModel.objects.filter(email__iexact=claims["email"].strip())

    def create_user(self, claims):
        email = claims["email"].strip().lower()
        user = self.UserModel.objects.create_user(email[:150], email=email)
        user.set_unusable_password()
        user.save(update_fields=["password"])
        return user

    def get_user(self, user_id):
        user = super().get_user(user_id)
        return user if user and user.is_active else None
