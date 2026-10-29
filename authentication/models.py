from django.conf import settings
from django.db import models
from django.utils import timezone

from common.fields import EncryptedTextField


class UserInvitation(models.Model):
    """
    Model representing a user invitation with company foreign key, invited email, role, token UUID,
    expiration time, and acceptance time.
    """

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE)
    invited_email = models.EmailField()
    role = models.CharField(max_length=100)
    token = models.UUIDField(unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['company', 'invited_email'], name='one_invitation_per_email_per_company'),
        ]

    def __str__(self):
        return f"Invitation for {self.invited_email} to join {self.company.name}"


class RefreshToken(models.Model):
    """
    A server-side record of an issued refresh token.

    Only a hash of the token is stored. Tokens are single use: refreshing revokes the presented
    token and issues a new one in the same ``family``. Presenting a token that was already
    revoked means it was replayed, so the whole family is revoked.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='refresh_tokens')
    token_hash = models.CharField(max_length=64, unique=True)
    family = models.UUIDField(db_index=True)
    password_stamp = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    # When the login that started this family happened; carried across rotations so a session has a hard cap.
    session_started_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    # Set (as well as revoked_at) when the token was revoked because it was exchanged for a new one,
    # as opposed to a logout or a detected replay.
    rotated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Refresh token for {self.user} (family {self.family})"


class TwoFactorDevice(models.Model):
    """
    A user's authenticator app (TOTP). It counts once ``confirmed_at`` is set, i.e. after the user proved they can
    read codes from it. ``last_used_step`` is the 30-second step of the last accepted code, so a code cannot be
    used twice. The secret is encrypted and never shown again after enrolment.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='two_factor')
    secret = EncryptedTextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Authenticator for {self.user}"


class RecoveryCode(models.Model):
    """A single-use code for when the authenticator is lost. Only a SHA-256 of it is stored."""

    device = models.ForeignKey(TwoFactorDevice, on_delete=models.CASCADE, related_name='recovery_codes')
    code_hash = models.CharField(max_length=64, unique=True)
    used_at = models.DateTimeField(null=True, blank=True)


class TwoFactorChallenge(models.Model):
    """
    Handed out by ``/auth/login/`` instead of tokens when a second step is needed: ``verify`` a code, or ``enrol``
    (an admin who has no authenticator yet). Short-lived, single use, and dead after too many wrong codes.
    """

    VERIFY = 'verify'
    ENROL = 'enrol'
    PURPOSES = [(VERIFY, 'Verify a code'), (ENROL, 'Set up an authenticator')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='two_factor_challenges')
    token_hash = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=10, choices=PURPOSES)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)


class TwoFactorEvent(models.Model):
    """Audit trail for two-factor sign-in: who set it up, turned it off, reset someone else's, or used a recovery code."""

    ENROLLED = 'enrolled'
    DISABLED = 'disabled'
    RESET = 'reset'
    RECOVERY_CODE_USED = 'recovery_code_used'
    KINDS = [
        (ENROLLED, 'Set up'),
        (DISABLED, 'Turned off'),
        (RESET, 'Reset by an admin'),
        (RECOVERY_CODE_USED, 'Recovery code used'),
    ]

    company = models.ForeignKey('tenants.Company', null=True, blank=True, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='two_factor_events')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        help_text='Who did it, when it was not the user themselves (an admin reset).',
    )
    kind = models.CharField(max_length=20, choices=KINDS)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"{self.get_kind_display()}: {self.user}"
