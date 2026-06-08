"""
config/inventory/serializers.py

This file contains Django REST Framework (DRF) serializers for the inventory app.
"""

from rest_framework import serializers
from .models import Item, Location, Supplier


class ItemSerializer(serializers.ModelSerializer):
    """
    Serializer for the Item model.
    """

    class Meta:
        model = Item
        fields = "__all__"


class LocationSerializer(serializers.ModelSerializer):
    """
    Serializer for the Location model.
    """

    class Meta:
        model = Location
        fields = "__all__"


class SupplierSerializer(serializers.ModelSerializer):
    """
    Serializer for the Supplier model.
    """

    class Meta:
        model = Supplier
        fields = "__all__"