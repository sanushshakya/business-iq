"""
config/common/serializers.py

This module contains Django REST Framework (DRF) serializers for common data across the project.
"""

from rest_framework import serializers

class CommonModelSerializer(serializers.Serializer):
    """
    Base serializer for common models.
    
    This serializer provides a basic structure for serializing common data attributes.
    """

    def to_representation(self, instance):
        """
        Convert an object into a dictionary of primitive datatypes.

        Args:
            instance (object): The object to serialize.

        Returns:
            dict: A dictionary representing the serialized object.
        """
        ret = super(CommonModelSerializer, self).to_representation(instance)
        # Add custom logic to include additional fields or modify existing ones
        return ret

class CommonDataSerializer(serializers.Serializer):
    """
    Serializer for common data.

    This serializer handles serialization of generic data structures used across different parts of the project.
    """

    def validate(self, data):
        """
        Validate incoming data.

        Args:
            data (dict): The data to validate.

        Returns:
            dict: Validated data.

        Raises:
            serializers.ValidationError: If validation fails.
        """
        # Add custom validation logic here
        return data

# Additional common serializers can be defined here