# common/serializers.py

from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .models import DemandAlert, Setting, StockAlert


class StockAlertSerializer(TenantModelSerializer):
    class Meta:
        model = StockAlert
        fields = '__all__'
        read_only_fields = ['created_at']


@extend_schema_serializer(component_name='BranchDemandAlert')
class DemandAlertSerializer(TenantModelSerializer):
    class Meta:
        model = DemandAlert
        fields = '__all__'
        read_only_fields = ['created_at', 'is_handled', 'event', 'cultural_event']


class VerificationTokenSerializer(serializers.Serializer):
    token = serializers.CharField()


class SettingSerializer(TenantModelSerializer):
    class Meta:
        model = Setting
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']
