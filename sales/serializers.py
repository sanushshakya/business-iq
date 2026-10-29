# sales/serializers.py

from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.tenancy import scope_queryset
from inventory.models import Product
from tenants.models import Till

from .models import Sale, SaleItem, TillSession
from .services import session_summary, vat_by_rate

MONEY = {'max_digits': 12, 'decimal_places': 2, 'min_value': Decimal('0.00')}


class VatRateTotalSerializer(serializers.Serializer):
    """One VAT rate's totals on a receipt or cash-up (money as strings)."""

    vat_code = serializers.CharField()
    rate = serializers.DecimalField(max_digits=5, decimal_places=2)
    net = serializers.DecimalField(max_digits=12, decimal_places=2)
    vat = serializers.DecimalField(max_digits=12, decimal_places=2)
    gross = serializers.DecimalField(max_digits=12, decimal_places=2)


class SessionSummarySerializer(serializers.Serializer):
    sales_count = serializers.IntegerField()
    sales_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    cash_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    card_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    voided_count = serializers.IntegerField()
    voided_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    expected_cash = serializers.DecimalField(max_digits=12, decimal_places=2)
    variance = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    vat_by_rate = VatRateTotalSerializer(many=True, help_text='Completed sales only.')
    vat_not_recorded_total = serializers.DecimalField(
        max_digits=12, decimal_places=2, help_text='Gross of lines sold before VAT was recorded.'
    )


class TillSessionSerializer(serializers.ModelSerializer):
    till_number = serializers.IntegerField(source='till.number', read_only=True)
    branch_name = serializers.CharField(source='till.branch.name', read_only=True)
    opened_by_email = serializers.EmailField(source='opened_by.email', read_only=True)
    opened_by_name = serializers.CharField(source='opened_by.display_name', read_only=True)
    closed_by_email = serializers.EmailField(source='closed_by.email', read_only=True, default=None)
    is_open = serializers.BooleanField(read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta:
        model = TillSession
        fields = [
            'id', 'till', 'till_number', 'branch_name', 'opened_by_email', 'opened_by_name', 'opened_at', 'opening_float',
            'closed_by_email', 'closed_at', 'expected_cash', 'counted_cash', 'note', 'is_open', 'summary',
        ]
        read_only_fields = fields

    @extend_schema_field(SessionSummarySerializer)
    def get_summary(self, obj):
        return SessionSummarySerializer(session_summary(obj)).data


class OpenSessionSerializer(serializers.Serializer):
    till = serializers.PrimaryKeyRelatedField(queryset=Till.objects.select_related('branch'))
    opening_float = serializers.DecimalField(default=Decimal('0.00'), **MONEY)

    def validate_till(self, till):
        if not scope_queryset(Till.objects.filter(pk=till.pk), self.context['request'].user).exists():
            raise serializers.ValidationError('Invalid pk - object does not exist.')
        return till


class CloseSessionSerializer(serializers.Serializer):
    counted_cash = serializers.DecimalField(**MONEY)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255, default='')


class SaleItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleItem
        fields = [
            'id', 'product', 'product_name', 'quantity', 'unit_price', 'line_total',
            'vat_code', 'vat_rate', 'net_amount', 'vat_amount',
        ]
        read_only_fields = fields


class SaleSerializer(serializers.ModelSerializer):
    receipt_number = serializers.CharField(read_only=True)
    till = serializers.IntegerField(source='session.till_id', read_only=True)
    till_number = serializers.IntegerField(source='session.till.number', read_only=True)
    branch_name = serializers.CharField(source='session.till.branch.name', read_only=True)
    cashier_email = serializers.EmailField(source='cashier.email', read_only=True)
    cashier_name = serializers.CharField(source='cashier.display_name', read_only=True)
    items = SaleItemSerializer(many=True, read_only=True)
    vat_summary = serializers.SerializerMethodField(help_text='VAT by rate, for the receipt.')
    vat_recorded = serializers.SerializerMethodField(help_text='False for sales made before VAT was recorded.')

    class Meta:
        model = Sale
        fields = [
            'id', 'receipt_number', 'session', 'till', 'till_number', 'branch_name', 'cashier_email', 'cashier_name',
            'payment_method', 'total', 'amount_tendered', 'change_due', 'status', 'created_at',
            'voided_at', 'void_reason', 'client_ref', 'items', 'vat_summary', 'vat_recorded',
        ]
        read_only_fields = fields

    @extend_schema_field(VatRateTotalSerializer(many=True))
    def get_vat_summary(self, sale):
        return VatRateTotalSerializer(vat_by_rate(sale.items.all()), many=True).data

    def get_vat_recorded(self, sale) -> bool:
        return all(item.vat_rate is not None for item in sale.items.all())


class SaleLineSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1, max_value=10000)


class CreateSaleSerializer(serializers.Serializer):
    session = serializers.PrimaryKeyRelatedField(queryset=TillSession.objects.select_related('till__branch'))
    payment_method = serializers.ChoiceField(choices=Sale.PAYMENT_METHODS)
    amount_tendered = serializers.DecimalField(required=False, allow_null=True, **MONEY)
    items = SaleLineSerializer(many=True, allow_empty=False, max_length=200)
    client_ref = serializers.UUIDField(required=False, allow_null=True, default=None)
    loyalty_barcode = serializers.CharField(required=False, allow_blank=True, max_length=32, default='')

    def validate_session(self, session):
        user = self.context['request'].user
        if not scope_queryset(TillSession.objects.filter(pk=session.pk), user).exists():
            raise serializers.ValidationError('Invalid pk - object does not exist.')
        return session

    def validate(self, attrs):
        # Products must belong to the till's company (this also guards superusers, who are not scoped).
        company_id = attrs['session'].till.branch.company_id
        for line in attrs['items']:
            if line['product'].company_id != company_id:
                raise serializers.ValidationError({'items': 'Invalid pk - object does not exist.'})
        return attrs


class VoidSaleSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255, default='')
