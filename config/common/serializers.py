"""
serializers.py

This file contains a basic serializer that can be used as a placeholder for other serializers in the iq project.
"""

from rest_framework import serializers

class PlaceholderSerializer(serializers.Serializer):
    """
    A basic serializer class that serves as a placeholder.

    This serializer does not perform any actual serialization tasks and is intended
    to be replaced with more specific serializers as needed.

    Fields:
        name (str): A string field to demonstrate serializer functionality.
    """
    name = serializers.CharField(max_length=100)