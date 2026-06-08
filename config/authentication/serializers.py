import re
from django.contrib.auth.models import User
from rest_framework import serializers

class RegisterUserSerializer(serializers.ModelSerializer):
    """
    Serializer for registering a new user with a company.

    This serializer includes fields for username, email, password, and company.
    """

    # Field to store the company name
    company = serializers.CharField(required=True)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'company']
        extra_kwargs = {
            'password': {'write_only': True},
        }

    def validate_username(self, value):
        """
        Validate the username to ensure it meets certain criteria.

        Args:
            value (str): The username to validate.

        Returns:
            str: The validated username.

        Raises:
            serializers.ValidationError: If the username is invalid.
        """
        if not re.match(r'^[a-zA-Z0-9_]+$', value):
            raise serializers.ValidationError("Username must be alphanumeric and may contain underscores.")
        return value

    def validate_email(self, value):
        """
        Validate the email to ensure it meets certain criteria.

        Args:
            value (str): The email to validate.

        Returns:
            str: The validated email.

        Raises:
            serializers.ValidationError: If the email is invalid.
        """
        if not re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', value):
            raise serializers.ValidationError("Invalid email format.")
        return value

    def validate_company(self, value):
        """
        Validate the company name to ensure it meets certain criteria.

        Args:
            value (str): The company name to validate.

        Returns:
            str: The validated company name.

        Raises:
            serializers.ValidationError: If the company name is invalid.
        """
        if not re.match(r'^[a-zA-Z0-9_ ]+$', value):
            raise serializers.ValidationError("Company name must be alphanumeric and may contain spaces and underscores.")
        return value

    def create(self, validated_data):
        """
        Create a new user instance with the provided validated data.

        Args:
            validated_data (dict): The validated data containing user information.

        Returns:
            User: The newly created user.
        """
        user = super().create(validated_data)
        user.set_password(validated_data['password'])
        user.save()
        return user