# pricing/serializers.py

from rest_framework import serializers

from .models import PricingPlan, Subscription, SupplierInvoice, InvoiceLineItem, PriceChangeLog


class PricingPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = PricingPlan
        fields = '__all__'


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = '__all__'


class SupplierInvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupplierInvoice
        fields = '__all__'


class InvoiceLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLineItem
        fields = '__all__'


class PriceChangeLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceChangeLog
        fields = '__all__'
