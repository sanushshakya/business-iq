from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .models import PurchaseOrder, PurchaseOrderLine, SupplierProduct


class SupplierProductSerializer(TenantModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)

    class Meta:
        model = SupplierProduct
        fields = ['id', 'supplier', 'supplier_name', 'product', 'product_name', 'pack_size', 'pack_cost', 'preferred']
        read_only_fields = ['id']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # Superusers are not scoped by the related fields: both sides must be the user's own company's.
        company_id = self.context['request'].user.company_id
        for field in ('supplier', 'product'):
            row = attrs.get(field) or getattr(self.instance, field, None)
            if row is not None and row.company_id != company_id:
                raise serializers.ValidationError({field: 'Not one of yours.'})
        return attrs


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    units = serializers.IntegerField(read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = ['id', 'product', 'product_name', 'packs', 'pack_size', 'units', 'pack_cost', 'line_cost',
                  'daily_demand', 'horizon_days', 'target', 'on_hand', 'on_order', 'events', 'stockout_date',
                  'topped_up']
        read_only_fields = fields


class PurchaseOrderSerializer(serializers.ModelSerializer):
    number_display = serializers.CharField(read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ['id', 'number', 'number_display', 'supplier', 'supplier_name', 'status', 'auto', 'edited',
                  'below_minimum', 'expected_on', 'total_cost', 'created_by', 'created_at', 'approved_by',
                  'approved_at', 'lines']
        read_only_fields = fields


class LineEditSerializer(serializers.Serializer):
    packs = serializers.IntegerField(min_value=0, max_value=100000, help_text='0 takes the line out.')


class DraftSummarySerializer(serializers.Serializer):
    created = serializers.IntegerField()
    replaced = serializers.IntegerField(help_text='Untouched drafts replaced by newer ones.')
    kept = serializers.IntegerField(help_text='Edited drafts left as they are.')
    skipped = serializers.IntegerField(help_text='Products with suppliers but none preferred.')
