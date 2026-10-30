# inventory/serializers.py


from collections.abc import Mapping

from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from . import transfers
from .ledger import move, virtual_location
from .models import (
    Location, Order, Product, ProductCategory, StockBatch, StockMovement, Supplier, Transfer, TransferLine,
)


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
    """
    A lot of a product at one location. ``location`` is set when the stock is received: leave it out and the
    company's only shop floor is used (with several, say which). It cannot be changed afterwards (moving stock is a
    transfer).
    """

    class Meta:
        model = StockBatch
        fields = '__all__'
        extra_kwargs = {
            'quantity': {'min_value': 0},
            'location': {'error_messages': {'required': 'Choose where this stock is received.'}},
        }

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:
            fields['location'].read_only = True  # moving stock between locations is a transfer, not an edit
        return fields

    def validate_location(self, location):
        if location.is_virtual:
            raise serializers.ValidationError('Stock is received at a real location, not a virtual one.')
        if not location.is_active:
            raise serializers.ValidationError('This location is no longer in use.')
        return location

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({'non_field_errors': ['Send a JSON object.']})
        # Fill the default before validation, so the (product, batch number, location) uniqueness check sees it.
        if self.instance is None and not data.get('location'):
            floors = list(Location.objects.filter(
                company=self.context['request'].user.company, type=Location.SHOP_FLOOR, is_active=True
            ).values_list('pk', flat=True)[:2])
            if len(floors) == 1:
                data = data.copy()  # works for a dict and for a (form) QueryDict
                data['location'] = floors[0]
        return super().to_internal_value(data)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        product = attrs.get('product') or getattr(self.instance, 'product', None)
        location = attrs.get('location') or getattr(self.instance, 'location', None)
        if product is not None and location is not None and product.company_id != location.company_id:
            raise serializers.ValidationError({'location': 'This location belongs to another company.'})
        return attrs


class StockMovementSerializer(TenantModelSerializer):
    """
    A movement is a ledger entry: creating one adds to (inbound) or takes from (outbound) its batch and
    the product's ``stock_quantity``. Entries cannot be edited or deleted; correct a mistake with a new one.
    """

    class Meta:
        model = StockMovement
        fields = '__all__'
        read_only_fields = ['source_location', 'destination_location']
        extra_kwargs = {'quantity': {'min_value': 1}}

    @transaction.atomic
    def create(self, validated_data):
        """Inbound comes from SUPPLIER into the batch's location; outbound goes from it to ADJUSTMENT."""
        # Lock the batch so two concurrent outbound movements cannot both take the last units.
        batch = (StockBatch.objects.select_for_update(of=('self',)).select_related('location')
                 .get(pk=validated_data['batch'].pk))
        quantity, movement_type = validated_data['quantity'], validated_data['movement_type']
        if movement_type == 'outbound' and quantity > batch.quantity:
            raise serializers.ValidationError(
                {'quantity': f"Only {batch.quantity} unit(s) left in batch {batch.batch_number}."})
        counterpart = virtual_location(
            batch.location.company_id, Location.SUPPLIER if movement_type == 'inbound' else Location.ADJUSTMENT
        )
        return move(batch, quantity, movement_type, counterpart=counterpart)


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


class TransferLineSerializer(TenantModelSerializer):
    class Meta:
        model = TransferLine
        fields = ['id', 'product', 'quantity', 'received_quantity', 'difference_reason']
        read_only_fields = ['id', 'received_quantity', 'difference_reason']
        extra_kwargs = {'quantity': {'min_value': 1}}


class TransferSerializer(TenantModelSerializer):
    """
    Stock moving between two of the company's own locations. Only a draft can be edited (sending ``lines`` replaces
    them); ``dispatch``, ``receive`` and ``cancel`` move it on. Numbers are per company (``reference`` T-000001).
    """

    reference = serializers.CharField(read_only=True)
    lines = TransferLineSerializer(many=True)

    class Meta:
        model = Transfer
        fields = ['id', 'number', 'reference', 'source_location', 'destination_location', 'status', 'note', 'lines',
                  'created_by', 'created_at', 'dispatched_by', 'dispatched_at', 'received_by', 'received_at',
                  'cancelled_by', 'cancelled_at']
        read_only_fields = ['id', 'number', 'status', 'created_by', 'created_at', 'dispatched_by', 'dispatched_at',
                            'received_by', 'received_at', 'cancelled_by', 'cancelled_at']

    @staticmethod
    def _lines(lines):
        return [(line['product'], line['quantity']) for line in lines]

    def create(self, validated_data):
        try:
            return transfers.create_transfer(
                company=validated_data['company'], user=self.context['request'].user,
                source=validated_data['source_location'], destination=validated_data['destination_location'],
                lines=self._lines(validated_data['lines']), note=validated_data.get('note', ''),
            )
        except transfers.TransferError as error:
            raise serializers.ValidationError(error.detail)

    def update(self, instance, validated_data):
        lines = (self._lines(validated_data['lines']) if 'lines' in validated_data
                 else [(line.product, line.quantity) for line in instance.lines.select_related('product')])
        try:
            return transfers.replace_draft(
                instance, source=validated_data.get('source_location', instance.source_location),
                destination=validated_data.get('destination_location', instance.destination_location),
                lines=lines, note=validated_data.get('note', instance.note),
            )
        except transfers.TransferError as error:
            raise serializers.ValidationError(error.detail)


class TransferReceiveLineSerializer(serializers.Serializer):
    line = serializers.IntegerField(help_text='A line id of this transfer.')
    received_quantity = serializers.IntegerField(min_value=0)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default='',
                                   help_text='Required when received differs from what was sent.')


class TransferReceiveSerializer(serializers.Serializer):
    """What arrived: every line of the transfer once."""

    lines = TransferReceiveLineSerializer(many=True)
