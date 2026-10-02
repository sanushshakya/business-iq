# demand/serializers.py

from django.utils import timezone
from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .models import CulturalEvent, Demand, EventProductKeyword


class DemandSerializer(TenantModelSerializer):
    class Meta:
        model = Demand
        fields = '__all__'

    def validate_quantity(self, value):
        if value < 0:
            raise serializers.ValidationError("Quantity cannot be negative.")
        return value

    def validate_due_date(self, value):
        # Only enforce on creation; existing demands may legitimately be past due.
        if self.instance is None and value < timezone.now().date():
            raise serializers.ValidationError("Due date must not be in the past.")
        return value


class CulturalEventSerializer(TenantModelSerializer):
    class Meta:
        model = CulturalEvent
        fields = '__all__'

    def validate(self, data):
        start = data.get('start_date', getattr(self.instance, 'start_date', None))
        end = data.get('end_date', getattr(self.instance, 'end_date', None))
        if start and end and end < start:
            raise serializers.ValidationError("end_date must not be before start_date.")
        return data


class EventProductKeywordSerializer(TenantModelSerializer):
    class Meta:
        model = EventProductKeyword
        fields = '__all__'
