# pricing/serializers.py

from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .models import PricingPlan, Subscription, SupplierInvoice, InvoiceLineItem, PriceChangeLog


class PricingPlanSerializer(TenantModelSerializer):
    class Meta:
        model = PricingPlan
        fields = '__all__'


class SubscriptionSerializer(TenantModelSerializer):
    class Meta:
        model = Subscription
        fields = '__all__'


class SupplierInvoiceSerializer(TenantModelSerializer):
    class Meta:
        model = SupplierInvoice
        fields = '__all__'


class InvoiceLineItemSerializer(TenantModelSerializer):
    class Meta:
        model = InvoiceLineItem
        fields = '__all__'


class PriceChangeLogSerializer(TenantModelSerializer):
    class Meta:
        model = PriceChangeLog
        fields = '__all__'
