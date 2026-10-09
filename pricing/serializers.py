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

    def _company_id(self, attrs):
        """The company the invoice belongs to: its own, the signed-in user's, or (superusers) the one supplied."""
        if self.instance is not None:
            return self.instance.company_id
        user = getattr(self.context.get('request'), 'user', None)
        if user is not None and user.company_id:
            return user.company_id
        company = attrs.get('company')
        return company.pk if company else None

    def validate(self, attrs):
        attrs = super().validate(attrs)  # per-company uniqueness (invoice_number)
        company_id = self._company_id(attrs)
        supplier = attrs.get('supplier') or getattr(self.instance, 'supplier', None)
        if supplier is not None and company_id and supplier.company_id != company_id:
            raise serializers.ValidationError({'supplier': 'This supplier belongs to a different company.'})
        return attrs


class InvoiceLineItemSerializer(TenantModelSerializer):
    class Meta:
        model = InvoiceLineItem
        fields = '__all__'


class PriceChangeLogSerializer(TenantModelSerializer):
    class Meta:
        model = PriceChangeLog
        fields = '__all__'
