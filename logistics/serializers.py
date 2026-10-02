# logistics/serializers.py

from rest_framework import serializers

from .models import FreightAlert, LogisticProvider, Delivery


class FreightAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = FreightAlert
        fields = '__all__'


class LogisticProviderSerializer(serializers.ModelSerializer):
    class Meta:
        model = LogisticProvider
        fields = '__all__'


class DeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = Delivery
        fields = '__all__'
