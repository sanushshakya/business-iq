from django.contrib.auth import authenticate
from django.utils import timezone
from rest_framework import serializers

from .models import UserInvitation


class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    Serializer for password reset confirmation.

    Expects the base64-encoded user id and token from the reset email, plus the
    new password entered twice.
    """

    uidb64 = serializers.CharField()
    token = serializers.CharField()
    new_password1 = serializers.CharField(min_length=8, write_only=True)
    new_password2 = serializers.CharField(min_length=8, write_only=True)

    def validate(self, data):
        """Ensure both password fields match."""
        if data['new_password1'] != data['new_password2']:
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


class RefreshTokenSerializer(serializers.Serializer):
    refresh_token = serializers.CharField()


class InvitationSerializer(serializers.ModelSerializer):
    """An invitation as shown to staff. The token is deliberately absent: it travels only by email."""

    status = serializers.SerializerMethodField()

    class Meta:
        model = UserInvitation
        fields = ['id', 'invited_email', 'role', 'status', 'expires_at', 'accepted_at']
        read_only_fields = fields

    def get_status(self, invitation) -> str:
        if invitation.accepted_at:
            return 'accepted'
        return 'expired' if invitation.expires_at <= timezone.now() else 'pending'


class InvitationCreateSerializer(serializers.Serializer):
    invited_email = serializers.EmailField()
    role = serializers.RegexField(r'^[\w .-]+$', max_length=100, help_text='Name of the group the new user joins.')


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
