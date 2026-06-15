# demand_calendar/serializers.py

from rest_framework import serializers
from .models import Event, DemandAlert

class DemandAlertSerializer(serializers.ModelSerializer):
    """
    Serializer for the DemandAlert model.

    This serializer is used to convert DemandAlert objects into JSON and vice versa.
    """

    class Meta:
        model = DemandAlert
        fields = '__all__'


class EventSerializer(serializers.ModelSerializer):
    """
    Serializer for the Event model.

    This serializer is used to convert Event objects into JSON and vice versa.
    """

    demand_alerts = DemandAlertSerializer(many=True, read_only=True)

    class Meta:
        model = Event
        fields = ['id', 'name', 'start_date', 'end_date', 'product_categories', 'demand_multiplier', 'demand_alerts']
