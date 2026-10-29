# inventory/serializers.py


from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import F
from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .models import Location, Order, Product, ProductCategory, StockBatch, StockMovement, Supplier


class ProductCategorySerializer(TenantModelSerializer):
    class Meta:
        model = ProductCategory
        fields = '__all__'


class ProductSerializer(TenantModelSerializer):
    class Meta:
        model = Product
        exclude = ['external_id']  # set by the import command only
        extra_kwargs = {
            'price': {'min_value': 0},
            'stock_quantity': {'min_value': 0},
        }


class OrderSerializer(TenantModelSerializer):
    class Meta:
        model = Order
        fields = '__all__'
        extra_kwargs = {'quantity': {'min_value': 1}}


class SupplierSerializer(TenantModelSerializer):
    class Meta:
        model = Supplier
        fields = '__all__'


class StockBatchSerializer(TenantModelSerializer):
    class Meta:
        model = StockBatch
        fields = '__all__'
        extra_kwargs = {'quantity': {'min_value': 0}}


class StockMovementSerializer(TenantModelSerializer):
    """
    A movement is a ledger entry: creating one adds to (inbound) or takes from (outbound) its batch and
    the product's ``stock_quantity``. Entries cannot be edited or deleted; correct a mistake with a new one.
    """

    class Meta:
        model = StockMovement
        fields = '__all__'
        extra_kwargs = {'quantity': {'min_value': 1}}

    @transaction.atomic
    def create(self, validated_data):
        # Lock the batch so two concurrent outbound movements cannot both take the last units.
        batch = StockBatch.objects.select_for_update().get(pk=validated_data['batch'].pk)
        delta = validated_data['quantity']
        if validated_data['movement_type'] == 'outbound':
            if delta > batch.quantity:
                raise serializers.ValidationError(
                    {'quantity': f"Only {batch.quantity} unit(s) left in batch {batch.batch_number}."})
            delta = -delta
        StockBatch.objects.filter(pk=batch.pk).update(quantity=F('quantity') + delta)
        Product.objects.filter(pk=batch.product_id).update(stock_quantity=F('stock_quantity') + delta)
        return super().create(validated_data)


class StockProjectionPointSerializer(serializers.Serializer):
    date = serializers.DateField()
    projected_quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    expected_demand = serializers.DecimalField(max_digits=12, decimal_places=2)
    demand_multiplier = serializers.DecimalField(max_digits=5, decimal_places=2)


class StockProjectionSerializer(serializers.Serializer):
    """Response of ``/inventory/products/{id}/stock-projection/``."""

    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    current_stock = serializers.IntegerField()
    reorder_threshold = serializers.IntegerField()
    average_daily_demand = serializers.DecimalField(max_digits=12, decimal_places=2)
    days_of_cover = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    stockout_date = serializers.DateField(allow_null=True)
    reorder_date = serializers.DateField(allow_null=True)
    projection = StockProjectionPointSerializer(many=True)


class LocationSerializer(TenantModelSerializer):
    """
    A stock location. Codes are stored in capitals and are unique within the company. Virtual locations (supplier,
    customer, waste, adjustment, in transit) are created by the system and cannot be added, changed or removed.
    """

    code = serializers.CharField(max_length=20, help_text='Short code, unique within the company (e.g. WH-MAIN).')
    is_virtual = serializers.BooleanField(read_only=True)

    class Meta:
        model = Location
        fields = ['id', 'code', 'name', 'type', 'branch', 'sellable', 'online_sellable', 'is_active', 'is_virtual',
                  'created_at']
        read_only_fields = ['id', 'is_virtual', 'created_at']

    def validate_code(self, value):
        return value.strip().upper()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is not None and self.instance.is_virtual:
            raise serializers.ValidationError('Virtual locations are managed by the system and cannot be changed.')
        if attrs.get('type') in Location.VIRTUAL_TYPES:
            raise serializers.ValidationError({'type': 'Virtual locations are created by the system.'})
        # Check the model's rules on the location as it would be saved (the stored one plus the changes).
        if self.instance is not None:
            location = Location(**{f.attname: getattr(self.instance, f.attname) for f in Location._meta.concrete_fields})
        else:
            location = Location(company=self.context['request'].user.company)
        for name, value in attrs.items():
            setattr(location, name, value)
        try:
            location.clean_fields(exclude=['company', 'branch'])
            location.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict)
        return attrs
