# authentication/refresh_tokens.py

"""
Issuing, rotating and revoking refresh tokens.

Refresh tokens are random opaque strings; only their SHA-256 is stored. Each token can be used
once. See ``authentication.models.RefreshToken``.
"""

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .jwt_handler import access_token_ttl, encode_token, password_stamp
from .models import RefreshToken


class InvalidRefreshToken(Exception):
    """The presented refresh token is unknown, expired, revoked, or no longer allowed."""


def refresh_token_ttl():
    return timedelta(seconds=settings.JWT_REFRESH_TOKEN_TTL_SECONDS)


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def _issue(user, family):
    raw = secrets.token_urlsafe(48)
    RefreshToken.objects.create(
        user=user,
        token_hash=_hash(raw),
        family=family,
        password_stamp=password_stamp(user),
        expires_at=timezone.now() + refresh_token_ttl(),
    )
    return raw


def issue_token_pair(user, family=None):
    """Return the response body for a fresh access + refresh token."""
    return {
        'access_token': encode_token(user),
        'refresh_token': _issue(user, family or uuid.uuid4()),
        'token_type': 'Bearer',
        'expires_in': int(access_token_ttl().total_seconds()),
        'refresh_expires_in': int(refresh_token_ttl().total_seconds()),
    }


def revoke_family(family):
    return RefreshToken.objects.filter(family=family, revoked_at__isnull=True).update(revoked_at=timezone.now())


def revoke_all_for_user(user):
    return RefreshToken.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())


def rotate(raw):
    """
    Exchange a refresh token for a new token pair; the presented token can never be used again.

    Raises ``InvalidRefreshToken``. A replayed (already used or revoked) token revokes its whole
    family, since either the legitimate client or an attacker is holding a stolen copy.
    """
    family_to_revoke = None
    with transaction.atomic():
        try:
            token = RefreshToken.objects.select_for_update().select_related('user').get(token_hash=_hash(raw))
        except RefreshToken.DoesNotExist:
            raise InvalidRefreshToken('unknown token')

        user = token.user
        if token.revoked_at is not None:
            reason = 'token reuse detected'
        elif token.expires_at <= timezone.now() or not user.is_active or token.password_stamp != password_stamp(user):
            reason = 'token no longer valid'
        else:
            token.revoked_at = timezone.now()
            token.save(update_fields=['revoked_at'])
            return user, issue_token_pair(user, family=token.family)
        family_to_revoke = token.family

    # Revoke outside the atomic block: raising inside it would roll the revocation back.
    revoke_family(family_to_revoke)
    raise InvalidRefreshToken(reason)


def logout(raw):
    """Revoke the whole session (token family) that ``raw`` belongs to. Unknown tokens are ignored."""
    token = RefreshToken.objects.filter(token_hash=_hash(raw)).first()
    if token is not None:
        revoke_family(token.family)


def prune(older_than=timedelta(days=7)):
    """Delete tokens that expired, or were revoked, more than ``older_than`` ago. Returns the count."""
    cutoff = timezone.now() - older_than
    expired = RefreshToken.objects.filter(expires_at__lt=cutoff)
    revoked = RefreshToken.objects.filter(revoked_at__lt=cutoff)
    count = expired.count() + revoked.exclude(pk__in=expired.values('pk')).count()
    expired.delete()
    revoked.delete()
    return count
