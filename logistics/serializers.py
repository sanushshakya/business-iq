# logistics/serializers.py

from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .models import FreightAlert, LogisticProvider, Delivery


class FreightAlertSerializer(TenantModelSerializer):
    class Meta:
        model = FreightAlert
        fields = '__all__'


class LogisticProviderSerializer(TenantModelSerializer):
    class Meta:
        model = LogisticProvider
        fields = '__all__'


class DeliverySerializer(TenantModelSerializer):
    class Meta:
        model = Delivery
        fields = '__all__'
