"""
Serializers for the `demand` app in the `iq` project.
"""

from rest_framework import serializers

# Assuming there is a model named `Demand` in the `demand/models.py`
from .models import Demand


class DemandSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Demand` model.

    This serializer handles the serialization and deserialization of
    the `Demand` model, which represents demand data within the system.
    """

    class Meta:
        """
        Metadata for the serializer.

        Specifies the model and fields to be included in serialization.
        """

        model = Demand  # Model this serializer operates on
        fields = "__all__"  # Include all fields from the `Demand` model

    def validate_quantity(self, value):
        """
        Validate that the quantity field is not negative.

        Args:
            value (int): The quantity to be validated.

        Returns:
            int: The validated quantity.
        """

        if value < 0:
            raise serializers.ValidationError("Quantity cannot be negative.")
        return value

    def validate_due_date(self, value):
        """
        Validate that the due date is in the future.

        Args:
            value (datetime.date): The due date to be validated.

        Returns:
            datetime.date: The validated due date.
        """

        from django.utils import timezone

        now = timezone.now().date()
        if value < now:
            raise serializers.ValidationError("Due date must be in the future.")
        return value