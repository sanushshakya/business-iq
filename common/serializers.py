# common/serializers.py

from rest_framework import serializers
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    Serializer for handling password reset confirmation requests.

    This serializer validates the data received during the password reset confirmation process.
    """

    uid = serializers.CharField(max_length=128)
    token = serializers.CharField(max_length=255)
    new_password1 = serializers.CharField(min_length=8, max_length=128)
    new_password2 = serializers.CharField(min_length=8, max_length=128)

    def validate(self, data):
        """
        Validates the data received during the password reset confirmation process.

        Ensures that both new passwords match and meet the minimum length requirement.
        """

        if data['new_password1'] != data['new_password2']:
            raise ValidationError("The two password fields didn't match.")

        # You can add additional validation logic here, such as checking complexity requirements

        return data

    def save(self):
        """
        Saves the new password for the user after successful validation.

        Updates the user's password and performs any other necessary actions.
        """

        user = User.objects.get(uid=self.validated_data['uid'])
        user.set_password(self.validated_data['new_password1'])
        user.save()