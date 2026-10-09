# authentication/emails.py

import logging

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)


def _send(subject, body, recipient):
    """Send one email. Returns False (and logs) instead of raising, so a mail outage never breaks a request."""
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
        return True
    except Exception:  # noqa: BLE001 - SMTP errors are many and varied
        logger.exception("Could not send email to %s", recipient)
        return False


def send_invitation_email(invitation) -> bool:
    link = settings.INVITATION_ACCEPT_URL.format(token=invitation.token)
    body = (
        f"You have been invited to join {invitation.company.name}.\n\n"
        f"Accept the invitation and choose a password here:\n{link}\n\n"
        f"This link expires on {invitation.expires_at:%d %B %Y}."
    )
    return _send(f"Invitation to join {invitation.company.name}", body, invitation.invited_email)


def send_password_reset_email(user) -> bool:
    link = settings.PASSWORD_RESET_URL.format(
        uid=urlsafe_base64_encode(force_bytes(user.pk)), token=default_token_generator.make_token(user)
    )
    body = (
        "Someone asked to reset the password for this account.\n\n"
        f"Choose a new password here:\n{link}\n\n"
        "If that was not you, ignore this email: your password is unchanged."
    )
    return _send("Reset your password", body, user.email)
