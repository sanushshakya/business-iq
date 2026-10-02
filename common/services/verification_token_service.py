# common/services/verification_token_service.py

"""
Generate and verify signed, time-limited tokens (e.g. for email verification) using
Django's TimestampSigner.
"""

from typing import Optional

from django.core.signing import BadSignature, TimestampSigner


class VerificationTokenService:
    SALT = 'verification_token_service_salt'
    MAX_AGE_SECONDS = 24 * 60 * 60

    @classmethod
    def _signer(cls):
        return TimestampSigner(salt=cls.SALT)

    @classmethod
    def generate_token(cls, user_id: int) -> str:
        """Return a signed token embedding the user id and the time it was issued."""
        return cls._signer().sign(str(user_id))

    @classmethod
    def verify_token(cls, token: str, max_age: Optional[int] = None) -> Optional[int]:
        """Return the user id for a valid, unexpired token, or None."""
        try:
            value = cls._signer().unsign(token, max_age=max_age or cls.MAX_AGE_SECONDS)
            return int(value)
        except (BadSignature, ValueError):  # SignatureExpired is a BadSignature subclass
            return None
