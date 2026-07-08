from rest_framework import serializers

class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    Serializer for handling password reset confirmation requests.

    Fields:
    - token: CharField representing the password reset token provided by email.
    - new_password1: CharField representing the first instance of the new password.
    - new_password2: CharField representing the second instance of the new password to confirm it.
    """

    token = serializers.CharField(max_length=255)
    new_password1 = serializers.CharField(min_length=8, write_only=True)
    new_password2 = serializers.CharField(min_length=8, write_only=True)

    def validate(self, data):
        """
        Validate that the two provided new passwords match.
        """

        if data['new_password1'] != data['new_password2']:
            raise serializers.ValidationError({"password": "The two password fields didn't match."})

        return data

    def save(self, **kwargs):
        """
        Save the new password for the user identified by the token.

        Args:
        - kwargs: Any additional keyword arguments to pass to the parent method.
        """

        from authentication.models import ShopifyConnection
        from auth.password import hash_password

        # Extract the token and user email from the context data passed during serialization
        token = self.validated_data.get('token')
        new_password = self.validated_data.get('new_password1')

        try:
            shopify_connection = ShopifyConnection.objects.get(password_reset_token=token)
            shopify_connection.user.set_password(new_password)  # Use Django's built-in method for setting password
            shopify_connection.password_reset_token = None  # Clear the token after successful password reset
            shopify_connection.save()
        except ShopifyConnection.DoesNotExist:
            raise serializers.ValidationError({"token": "Invalid or expired password reset token."})

        return shopify_connection

    def validate_token(self, value):
        """
        Validate that the provided token is valid and has not expired.
        """

        from authentication.models import ShopifyConnection
        try:
            shopify_connection = ShopifyConnection.objects.get(password_reset_token=value)
            if shopify_connection.password_reset_expiration < timezone.now():
                raise serializers.ValidationError({"token": "Expired password reset token."})
        except ShopifyConnection.DoesNotExist:
            raise serializers.ValidationError({"token": "Invalid or expired password reset token."})

        return value