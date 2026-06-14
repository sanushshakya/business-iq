# common/serializers.py

from rest_framework import serializers
from .models import StockProjection

class StockProjectionSerializer(serializers.ModelSerializer):
    """
    Serializer for the StockProjection model.

    This serializer is used to convert StockProjection objects into JSON and vice versa.
    """

    class Meta:
        model = StockProjection  # Specify the model this serializer will serialize
        fields = '__all__'  # Include all fields from the StockProjection model in the serialized data

# Inline comments explaining each section of the code