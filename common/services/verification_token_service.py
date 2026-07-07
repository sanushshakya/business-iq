"""
common/services/verification_token_service.py
=========================================

Service to generate and sign verification tokens using Django's TimestampSigner.

This service provides a way to create and verify tokens that can be used for email 
verification or other purposes where a temporary, time-limited token is required.
"""

from django.core.signing import TimestampSigner
from django.utils.crypto import get_random_string

class VerificationTokenService:
    """
    Service class for generating and verifying verification tokens.

    The service uses Django's TimestampSigner to ensure that the token includes an expiration timestamp,
    making it time-limited. Additionally, a random salt is included to prevent tampering.
    """

    SALT = 'verification_token_service_salt'
    SIGNER_KWARGS = {'salt': SALT}

    @staticmethod
    def generate_token(user_id: int) -> str:
        """
        Generate a verification token for the given user ID.

        Args:
            user_id (int): The ID of the user to generate a token for.

        Returns:
            str: A signed, time-limited token.
        """
        signer = TimestampSigner(**VerificationTokenService.SIGNER_KWARGS)
        token = f'{user_id}_{get_random_string()}'
        return signer.sign(token)

    @staticmethod
    def verify_token(token: str) -> int:
        """
        Verify the given verification token and return the user ID if valid.

        Args:
            token (str): The token to verify.

        Returns:
            int: The user ID associated with the verified token, or None if invalid.
        """
        signer = TimestampSigner(**VerificationTokenService.SIGNER_KWARGS)
        try:
            signed_token = signer.unsign(token)
            user_id, salt = signed_token.split('_', 1)
            if salt == VerificationTokenService.SALT:
                return int(user_id)
        except (ValueError, SignatureExpired):
            pass
        return None