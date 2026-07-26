# authentication/views.py

from django.contrib.auth import get_user_model
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .serializers import PasswordResetConfirmSerializer

User = get_user_model()

class PasswordResetConfirmView(generics.GenericAPIView):
    """
    View for handling password reset confirmation.

    This view allows a user to confirm their password reset request by providing the token and new password.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = PasswordResetConfirmSerializer

    def post(self, request, *args, **kwargs):
        """
        Handle POST requests to confirm password reset.

        Args:
            request (Request): The request object containing the serialized data.
        
        Returns:
            Response: A response indicating success or failure of the operation.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response({"detail": "Password reset confirmed successfully."}, status=status.HTTP_200_OK)


# authentication/serializers.py

from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_text, smart_bytes
from django.utils.http import urlsafe_base64_decode
from .views import PasswordResetConfirmView

class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    Serializer for handling password reset confirmation.

    This serializer validates the token and new password provided by a user during the password reset process.
    """
    
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(min_length=8)
    confirm_new_password = serializers.CharField()

    def validate(self, data):
        """
        Validate the input data.

        Args:
            data (dict): The data dictionary containing the serializer fields.
        
        Returns:
            dict: The validated data.
        
        Raises:
            serializers.ValidationError: If validation fails.
        """
        uid = force_text(urlsafe_base64_decode(data['uid']))
        user = User.objects.get(pk=uid)

        if not default_token_generator.check_token(user, data['token']):
            raise serializers.ValidationError({'token': ['Invalid token']})

        if data['new_password'] != data['confirm_new_password']:
            raise serializers.ValidationError({'passwords': ['Passwords do not match']})

        return data

    def save(self):
        """
        Save the new password.

        Returns:
            User: The updated user object.
        """
        validated_data = self.validated_data
        user = User.objects.get(pk=validated_data['uid'])
        user.set_password(validated_data['new_password'])
        user.save()
        return user

# authentication/urls.py

from django.urls import path
from .views import PasswordResetConfirmView

urlpatterns = [
    """
    URL patterns for handling password reset confirmations.

    This module defines the URL patterns for the password reset confirmation feature within a Django application.
    """

    # URL pattern for the password reset confirmation view
    path('password_reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]