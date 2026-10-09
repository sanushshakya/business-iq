# pricing/serializers.py

from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.urls import reverse
from rest_framework import serializers

from common.tenancy import TenantModelSerializer
from inventory.models import Product

from .models import InvoiceLineItem, PriceChangeLog, PricingPlan, Subscription, SupplierInvoice

# The first bytes of each file type we accept: the extension alone proves nothing.
FILE_SIGNATURES = {
    'pdf': (b'%PDF-',),
    'png': (b'\x89PNG\r\n\x1a\n',),
    'jpg': (b'\xff\xd8\xff',),
    'jpeg': (b'\xff\xd8\xff',),
}


class PricingPlanSerializer(TenantModelSerializer):
    class Meta:
        model = PricingPlan
        fields = '__all__'


class SubscriptionSerializer(TenantModelSerializer):
    class Meta:
        model = Subscription
        fields = '__all__'


class SupplierInvoiceSerializer(TenantModelSerializer):
    """
    An invoice from a supplier. ``file`` is upload-only (PDF, PNG or JPEG); read it back through ``file_url``,
    an authenticated download endpoint, because uploads are never served publicly.
    """

    file = serializers.FileField(write_only=True, required=False, allow_empty_file=False)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = SupplierInvoice
        fields = '__all__'

    def get_file_url(self, invoice) -> Optional[str]:
        if not invoice.file:
            return None
        request = self.context.get('request')
        path = reverse('supplierinvoice-file', args=[invoice.pk])
        return request.build_absolute_uri(path) if request else path

    def validate_file(self, upload):
        extension = upload.name.rsplit('.', 1)[-1].lower() if '.' in upload.name else ''
        if extension not in FILE_SIGNATURES:
            raise serializers.ValidationError('Upload a PDF, PNG or JPEG file.')
        if upload.size > settings.INVOICE_MAX_UPLOAD_BYTES:
            raise serializers.ValidationError(f'The file is larger than {settings.INVOICE_MAX_UPLOAD_BYTES // (1024 * 1024)} MB.')
        head = upload.read(8)
        upload.seek(0)
        if not any(head.startswith(signature) for signature in FILE_SIGNATURES[extension]):
            raise serializers.ValidationError(f'This does not look like a real .{extension} file.')
        return upload

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
    """
    A requested change to a product's price. Clients choose the product and the new price; ``old_price`` is taken
    from the product, and approval and processing only happen through the ``approve`` action and the Shopify sync.
    """

    class Meta:
        model = PriceChangeLog
        fields = '__all__'
        read_only_fields = ['old_price', 'changed_at', 'is_approved', 'is_processed']

    def validate_new_price(self, value):
        if value <= 0:
            raise serializers.ValidationError('The price must be greater than zero.')
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        batch, product = attrs.get('stock_batch'), attrs['product']
        if batch is not None and batch.product_id != product.pk:
            raise serializers.ValidationError({'stock_batch': 'This batch belongs to a different product.'})
        if attrs['new_price'] == product.price:
            raise serializers.ValidationError({'new_price': 'This is already the product price.'})
        return attrs


class PriceRecommendationRequestSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1, default=1, help_text='Units in the consignment the duty is worked out for.')
    margin_percent = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False, min_value=Decimal('0.01'), max_value=Decimal('1000'),
        help_text="Markup on landed cost. Defaults to the company's 'default_margin_percent' setting, then 30.",
    )
    origin = serializers.RegexField(
        r'^[A-Za-z]{2}$', required=False, help_text='ISO country code of origin, to apply a trade-deal duty rate.')


class PriceRecommendationSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    quantity = serializers.IntegerField()
    goods_cost = serializers.DecimalField(max_digits=14, decimal_places=2)
    duty_rate_percent = serializers.DecimalField(max_digits=6, decimal_places=2)
    duty_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    duty_source = serializers.CharField(help_text="'tariff', 'tariff_preference' or 'default' (an estimate: see note).")
    note = serializers.CharField(allow_blank=True)
    landed_cost_total = serializers.DecimalField(max_digits=14, decimal_places=2)
    landed_cost_per_unit = serializers.DecimalField(max_digits=14, decimal_places=2)
    margin_percent = serializers.DecimalField(max_digits=6, decimal_places=2)
    recommended_unit_price = serializers.DecimalField(max_digits=14, decimal_places=2)
    current_unit_price = serializers.DecimalField(max_digits=14, decimal_places=2)
