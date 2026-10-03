# authentication/jwt_handler.py

"""Issue and verify the short-lived access tokens used by the API."""

import hashlib
from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone

ALGORITHM = 'HS256'


def password_stamp(user):
    """
    Short fingerprint of the user's current password hash.

    Embedded in tokens so that changing or resetting a password invalidates every token issued
    before it.
    """
    return hashlib.sha256(user.password.encode()).hexdigest()[:16]


def access_token_ttl():
    return timedelta(seconds=settings.JWT_ACCESS_TOKEN_TTL_SECONDS)


def encode_token(user):
    """Return a signed token for ``user``, valid for ``JWT_ACCESS_TOKEN_TTL_SECONDS``."""
    now = timezone.now()
    payload = {'user_id': user.pk, 'pw': password_stamp(user), 'iat': now, 'exp': now + access_token_ttl()}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token):
    """Return the token payload. Raises ``jwt.InvalidTokenError`` if it is invalid or expired."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM], options={'require': ['exp', 'user_id', 'pw']})
