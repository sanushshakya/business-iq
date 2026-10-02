from django.contrib.auth import authenticate
from rest_framework import serializers


# Serializer for handling password reset confirmation requests
class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    Serializer class for password reset confirmation.

    This serializer ensures that all required fields are present and correctly formatted.
    """

    new_password = serializers.CharField(required=True, min_length=8)
    confirm_new_password = serializers.CharField(required=True, min_length=8)

    def validate(self, data):
        """
        Validates the input data to ensure the passwords match and meet length requirements.

        Args:
            data (dict): The input data containing new_password and confirm_new_password.

        Returns:
            dict: The validated data.
        """
        if data['new_password'] != data['confirm_new_password']:
            raise serializers.ValidationError("Passwords must match.")
        return data


class LoginUserSerializer(serializers.Serializer):
    """
    Serializer for logging in a user.

    This serializer validates user credentials and returns an authentication token.
    """

    username = serializers.CharField(required=True)
    password = serializers.CharField(write_only=True, required=True)

    def validate(self, data):
        """
        Validate the user credentials and return an authentication token.

        Args:
            data (dict): A dictionary containing 'username' and 'password'.

        Returns:
            dict: A dictionary containing the authentication token.
        """
        username = data.get('username')
        password = data.get('password')

        if not username or not password:
            raise serializers.ValidationError("Both username and password are required.")

        # Assuming `authenticate` is a function that checks credentials
        user = authenticate(username=username, password=password)
        if not user:
            raise serializers.ValidationError("Invalid credentials.")

        data['user'] = user
        return data
