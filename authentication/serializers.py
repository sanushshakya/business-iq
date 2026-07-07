from rest_framework import serializers

class VerificationTokenSerializer(serializers.Serializer):
    """
    Serializer for handling email verification tokens.

    Fields:
    - token: CharField representing the verification token.
    """

    token = serializers.CharField(max_length=255)

    def validate_token(self, value):
        """
        Validate that the provided token is valid and not expired.

        Args:
            value (str): The verification token to validate.

        Returns:
            str: The validated token.

        Raises:
            ValidationError: If the token is invalid or expired.
        """
        # TODO: Implement token validation logic here
        # For example, check if the token exists in the database and has not expired

        raise serializers.ValidationError("Invalid or expired verification token")