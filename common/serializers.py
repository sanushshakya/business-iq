# common/serializers.py

from rest_framework import serializers
from .models import DemandAlert

class DemandAlertSerializer(serializers.ModelSerializer):
    """
    Serializer for the DemandAlert model.

    This serializer is used to convert DemandAlert objects into JSON and vice versa.
    """

    class Meta:
        model = DemandAlert
        fields = [
            'id',
            'event_name',
            'product_name',
            'days_until_event',
            'recommended_order_quantity',
            # Add new fields here as needed
            'additional_field_1',
            'additional_field_2',
            # ...
        ]