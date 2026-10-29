# authentication/two_factor.py

"""
Two-factor sign-in (TOTP) for people who sign in with a password.

Required for admins (``is_staff`` / superusers), optional for other password users; operators (8-digit code) never
use it. ``/auth/login/`` hands out a short-lived challenge instead of tokens when a second step is needed, and the
views in ``two_factor_views.py`` trade it for tokens. Codes and secrets are never logged.
"""

import hashlib
import hmac
import secrets
import time
from datetime import timedelta

import pyotp
import segno
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from . import refresh_tokens
from .models import RecoveryCode, TwoFactorChallenge, TwoFactorDevice, TwoFactorEvent

ISSUER = 'Business IQ'
STEP_SECONDS = 30
CHALLENGE_TTL_SECONDS = 300
CHALLENGE_MAX_ATTEMPTS = 5
# Wrong codes per user (across challenges and sessions) before every code is refused for a while.
LOCKOUT_FAILURES = 10
LOCKOUT_SECONDS = 900
RECOVERY_CODE_COUNT = 10
RECOVERY_ALPHABET = 'abcdefghijklmnopqrstuvwxyz234567'


class TwoFactorError(Exception):
    """A two-factor action was refused. ``code`` says why (``invalid_code``, ``already_enabled``, ...)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class InvalidChallenge(Exception):
    """The challenge is unknown, expired, already used, for another purpose, or dead after too many wrong codes."""


def _now():
    """Seconds since the epoch; patched in tests to move the TOTP clock."""
    return time.time()


def _sha256(value):
    return hashlib.sha256(value.encode()).hexdigest()


def is_required(user):
    return not user.is_operator and (user.is_staff or user.is_superuser)


def _device(user, confirmed=True):
    return TwoFactorDevice.objects.filter(user=user, confirmed_at__isnull=not confirmed).first()


def is_enabled(user):
    return TwoFactorDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()


def recovery_codes_left(user):
    return RecoveryCode.objects.filter(device__user=user, device__confirmed_at__isnull=False, used_at__isnull=True).count()


def _audit(user, kind, actor=None):
    TwoFactorEvent.objects.create(company_id=user.company_id, user=user, actor=actor, kind=kind)


# --- lockout -------------------------------------------------------------------------------------------------------

def _fail_key(user):
    return f'2fa-fail:{user.pk}'


def is_locked(user):
    return cache.get(_fail_key(user), 0) >= LOCKOUT_FAILURES


def _record_user_failure(user):
    key = _fail_key(user)
    if cache.add(key, 1, LOCKOUT_SECONDS):
        return
    try:
        cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.add(key, 1, LOCKOUT_SECONDS)


# --- codes ---------------------------------------------------------------------------------------------------------

def _matching_step(device, code):
    """The time step ``code`` belongs to (now, or one step either side), or None. Constant-time comparisons."""
    code = ''.join(code.split())
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(device.secret)
    current = int(_now() // STEP_SECONDS)
    for step in (current - 1, current, current + 1):
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), code):
            return step
    return None


def _accept_code(device, code):
    """Check ``code`` against ``device`` and use up its time step. False for wrong, reused or locked out."""
    if is_locked(device.user):
        return False
    with transaction.atomic():
        device = TwoFactorDevice.objects.select_for_update().get(pk=device.pk)
        step = _matching_step(device, code)
        if step is None or (device.last_used_step is not None and step <= device.last_used_step):
            ok = False
        else:
            device.last_used_step = step
            device.save(update_fields=['last_used_step'])
            ok = True
    if ok:
        cache.delete(_fail_key(device.user))
    else:
        _record_user_failure(device.user)
    return ok


def check_code(user, code):
    """True if ``code`` is a current, unused code from the user's confirmed authenticator."""
    device = _device(user)
    return device is not None and _accept_code(device, code)


def _normalise_recovery_code(raw):
    return ''.join(ch for ch in raw.lower() if ch.isalnum())


def use_recovery_code(user, raw):
    """True (and the code is used up) if ``raw`` is one of the user's unused recovery codes."""
    if is_locked(user):
        return False
    code_hash = _sha256(_normalise_recovery_code(raw))
    used = RecoveryCode.objects.filter(
        device__user=user, device__confirmed_at__isnull=False, code_hash=code_hash, used_at__isnull=True,
    ).update(used_at=timezone.now())
    if not used:
        _record_user_failure(user)
        return False
    cache.delete(_fail_key(user))
    _audit(user, TwoFactorEvent.RECOVERY_CODE_USED)
    return True


def _new_recovery_code():
    raw = ''.join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
    return f'{raw[:5]}-{raw[5:]}'


# --- enrolment -----------------------------------------------------------------------------------------------------

def start_enrolment(user):
    """
    Create a new (unconfirmed) authenticator for ``user`` and return what the app needs to show:
    ``{secret, otpauth_uri, qr_svg}``. Replaces an earlier unconfirmed one; refused if one is already confirmed.
    """
    if is_enabled(user):
        raise TwoFactorError('already_enabled')
    TwoFactorDevice.objects.filter(user=user).delete()
    secret = pyotp.random_base32()
    TwoFactorDevice.objects.create(user=user, secret=secret)
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
    return {'secret': secret, 'otpauth_uri': uri, 'qr_svg': segno.make(uri, error='m').svg_inline(scale=5)}


def confirm_enrolment(user, code):
    """Switch the new authenticator on once ``code`` from it is right; return the 10 recovery codes (shown once)."""
    device = _device(user, confirmed=False)
    if device is None:
        raise TwoFactorError('not_started')
    if not _accept_code(device, code):
        raise TwoFactorError('invalid_code')
    codes = [_new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    with transaction.atomic():
        device.confirmed_at = timezone.now()
        device.save(update_fields=['confirmed_at'])
        RecoveryCode.objects.bulk_create(
            RecoveryCode(device=device, code_hash=_sha256(_normalise_recovery_code(c))) for c in codes
        )
        _audit(user, TwoFactorEvent.ENROLLED)
    return codes


def disable(user, code):
    """Turn two-factor off with a current code. Refused for admins, who must keep it."""
    if is_required(user):
        raise TwoFactorError('required')
    if not check_code(user, code):
        raise TwoFactorError('invalid_code')
    TwoFactorDevice.objects.filter(user=user).delete()
    _audit(user, TwoFactorEvent.DISABLED)


def reset(target, actor):
    """An admin removes ``target``'s authenticator (lost phone). Ends their sessions; they set it up again."""
    TwoFactorDevice.objects.filter(user=target).delete()
    TwoFactorChallenge.objects.filter(user=target, used_at__isnull=True).update(used_at=timezone.now())
    cache.delete(_fail_key(target))
    _audit(target, TwoFactorEvent.RESET, actor=actor)
    refresh_tokens.revoke_all_for_user(target)


# --- challenges ----------------------------------------------------------------------------------------------------

def issue_challenge(user, purpose):
    """Return a new raw challenge token for ``user``; only its SHA-256 is stored."""
    raw = secrets.token_urlsafe(32)
    TwoFactorChallenge.objects.create(
        user=user, token_hash=_sha256(raw), purpose=purpose,
        expires_at=timezone.now() + timedelta(seconds=CHALLENGE_TTL_SECONDS),
    )
    return raw


def load_challenge(raw, purpose):
    """The live challenge for ``raw`` with this purpose, or ``InvalidChallenge``."""
    challenge = (
        TwoFactorChallenge.objects.select_related('user')
        .filter(token_hash=_sha256(raw or ''), purpose=purpose, used_at__isnull=True,
                expires_at__gt=timezone.now(), attempts__lt=CHALLENGE_MAX_ATTEMPTS, user__is_active=True)
        .first()
    )
    if challenge is None:
        raise InvalidChallenge()
    return challenge


def record_failure(challenge):
    TwoFactorChallenge.objects.filter(pk=challenge.pk).update(attempts=F('attempts') + 1)
    challenge.attempts += 1


def consume(challenge):
    """Use the challenge up. Atomic: a challenge raced by two requests is consumed by only one (returns False)."""
    return bool(TwoFactorChallenge.objects.filter(pk=challenge.pk, used_at__isnull=True).update(used_at=timezone.now()))
