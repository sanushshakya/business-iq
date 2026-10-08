from django.conf import settings
from django.db import models
from django.utils import timezone


class UserInvitation(models.Model):
    """
    Model representing a user invitation with company foreign key, invited email, role, token UUID,
    expiration time, and acceptance time.
    """

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE)
    invited_email = models.EmailField(unique=True)
    role = models.CharField(max_length=100)
    token = models.UUIDField(unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

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
