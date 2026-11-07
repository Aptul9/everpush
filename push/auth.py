from django.conf import settings
from django.contrib.auth.backends import ModelBackend


class OwnerBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        email = (username or "").strip().lower()
        if email != settings.OWNER_EMAIL:
            from django.contrib.auth.hashers import make_password
            make_password(password or "")
            return None
        return super().authenticate(request, username=email, password=password, **kwargs)

    def get_user(self, user_id):
        user = super().get_user(user_id)
        return user if user and user.username == settings.OWNER_EMAIL else None
