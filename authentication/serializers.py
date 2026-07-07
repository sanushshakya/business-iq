from rest_framework import serializers
from .models import User

class UserSerializer(serializers.ModelSerializer):
    """
    Serializer for the User model.

    Includes additional fields:
    - is_verified: BooleanField indicating whether the user has been verified.
    """

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'is_active', 'is_staff', 'is_verified']

    def to_representation(self, instance):
        """
        Include the is_verified field in the serialized representation of a user.

        :param instance: The User instance to serialize.
        :return: Dictionary representing the serialized user data including is_verified.
        """
        ret = super().to_representation(instance)
        ret['is_verified'] = instance.is_verified
        return ret