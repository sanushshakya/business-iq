"""
serializers.py

This file contains Django REST Framework (DRF) serializers for the logistics app.
"""

from rest_framework import serializers
from .models import Logistic


class LogisticSerializer(serializers.ModelSerializer):
    """
    Serializer for the Logistic model.

    This serializer converts Logistic model instances into JSON format and vice versa.
    """

    class Meta:
        model = Logistic
        fields = "__all__"