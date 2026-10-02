# common/serializers.py

from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

from .models import DemandAlert, StockAlert


class StockAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockAlert
        fields = '__all__'
        read_only_fields = ['created_at']


@extend_schema_serializer(component_name='BranchDemandAlert')
class DemandAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = DemandAlert
        fields = '__all__'
        read_only_fields = ['created_at', 'is_handled']


class VerificationTokenSerializer(serializers.Serializer):
    token = serializers.CharField()
