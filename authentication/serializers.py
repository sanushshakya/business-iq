from django.urls import path
from .views import PasswordResetConfirmView
from rest_framework import serializers

# Module docstring
"""
URL patterns for handling password reset confirmations.

This module defines the URL patterns for the password reset confirmation feature within a Django application.
"""

urlpatterns = [
    # URL pattern for the password reset confirmation view
    path('password_reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]

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

# Update the views.py to use the serializer
from django.urls import path
from .views import PasswordResetConfirmView
from .serializers import PasswordResetConfirmSerializer

class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    """
    Custom view for password reset confirmation.

    This view uses a custom serializer to validate the input data.
    """

    serializer_class = PasswordResetConfirmSerializer

urlpatterns = [
    # URL pattern for the password reset confirmation view
    path('password_reset/confirm/', CustomPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]