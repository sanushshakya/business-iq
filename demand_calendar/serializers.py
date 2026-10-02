# demand_calendar/serializers.py

from rest_framework import serializers

from .models import DemandAlert, Event


class DemandAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = DemandAlert
        fields = '__all__'


class EventSerializer(serializers.ModelSerializer):
    demand_alerts = DemandAlertSerializer(many=True, read_only=True)

    class Meta:
        model = Event
        fields = ['id', 'name', 'start_date', 'end_date', 'product_categories', 'demand_multiplier', 'demand_alerts']
