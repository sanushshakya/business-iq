# inventory/serializers.py


from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from . import requisitions, transfers
from .ledger import move, virtual_location
from .models import (
    BarcodeRule, Breakdown, BreakdownLine, Location, YieldTemplate, YieldTemplateOutput, weighed_product_problem, yield_output_problems, Order, Product, ProductCategory, Requisition, RequisitionLine, StockBatch, StockMovement, Supplier,
    Transfer, TransferLine, normalise_plu,
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
            'nominal_weight_kg': {'min_value': Decimal('0.001')},
        }

    def validate_plu(self, plu):
        if not plu:
            return ''
        try:
            return normalise_plu(plu)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict['plu']) from None

    def validate(self, attrs):
        attrs = super().validate(attrs)

        def value(name):
            return attrs[name] if name in attrs else getattr(self.instance, name, Product._meta.get_field(name).get_default())

        # The PLU constraint is conditional (blank allowed many times), which the tenancy check skips.
        company_id = self._company_id_for(attrs)
        if attrs.get('plu') and company_id and Product.objects.filter(company_id=company_id, plu=attrs['plu']).exclude(
                pk=getattr(self.instance, 'pk', None)).exists():
            raise serializers.ValidationError({'plu': 'Another product already has this PLU.'})
        weighed = value('sold_by') == Product.WEIGHT
        if weighed and not (value('price_per_kg') or 0) > 0:
            raise serializers.ValidationError({'price_per_kg': 'A product sold by weight needs a price per kg.'})
        if value('is_catch_weight') and not weighed:
            raise serializers.ValidationError({'is_catch_weight': 'Only a product sold by weight can be catch-weight.'})
        if self.instance is not None:
            # Kg and counts mean different things per kind, so the kind is fixed while any stock is held.
            for name in ('sold_by', 'is_catch_weight'):
                if name in attrs and attrs[name] != getattr(self.instance, name) and self._kind_in_use():
                    raise serializers.ValidationError(
                        {name: 'This product holds stock or has a sale that can still be voided; empty its stock '
                               'and close the till sessions first.'})
        return attrs

    def _kind_in_use(self):
        """Stock held, or a completed sale in an open session (a void would put its units back)."""
        from sales.models import Sale, SaleAllocation

        return StockBatch.objects.filter(Q(quantity__gt=0) | Q(weight_kg__gt=0), product=self.instance).exists() or (
            SaleAllocation.objects.filter(item__product=self.instance, item__sale__status=Sale.COMPLETED,
                                          item__sale__session__closed_at__isnull=True).exists()
        )


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
            'weight_kg': {'min_value': 0},
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
        if self.instance is not None and self.instance.location.is_virtual:
            raise serializers.ValidationError('Stock at a virtual location (e.g. in transit) is moved only by its transfer.')
        if product is not None:
            self._check_kind(product, attrs)
        return attrs

    def _check_kind(self, product, attrs):
        """Each products hold a count, loose ones kg only, catch-weight ones pieces and their kg."""
        def value(name):
            return attrs[name] if name in attrs else getattr(self.instance, name, 0)

        quantity, weight = value('quantity'), value('weight_kg')
        if not product.is_weighed:
            if weight:
                raise serializers.ValidationError({'weight_kg': f'{product} is sold by each; leave the kg out.'})
            return
        if self.instance is None and not weight:
            raise serializers.ValidationError({'weight_kg': f'{product} is sold by weight; enter the kg received.'})
        if not product.is_catch_weight and quantity:
            raise serializers.ValidationError({'quantity': f'{product} is sold loose by weight; the count stays 0.'})
        if product.is_catch_weight and self.instance is None and not quantity:
            raise serializers.ValidationError({'quantity': f'{product} is catch-weight; enter how many pieces.'})


class StockMovementSerializer(TenantModelSerializer):
    """
    A movement is a ledger entry: creating one adds to (inbound) or takes from (outbound) its batch and
    the product's ``stock_quantity``. Entries cannot be edited or deleted; correct a mistake with a new one.
    """

    class Meta:
        model = StockMovement
        fields = '__all__'
        read_only_fields = ['source_location', 'destination_location']
        extra_kwargs = {'quantity': {'min_value': 0}, 'weight_kg': {'min_value': 0}}

    def validate_batch(self, batch):
        if batch.location.is_virtual:
            raise serializers.ValidationError('Stock at a virtual location (e.g. in transit) is moved only by its transfer.')
        return batch

    def validate(self, attrs):
        attrs = super().validate(attrs)
        product, quantity, weight = attrs['batch'].product, attrs.get('quantity', 0), attrs.get('weight_kg')
        if not product.is_weighed:
            if weight is not None:
                raise serializers.ValidationError({'weight_kg': f'{product} is sold by each; leave the kg out.'})
            if quantity < 1:
                raise serializers.ValidationError({'quantity': 'Move at least 1 unit.'})
        elif weight is None:
            raise serializers.ValidationError({'weight_kg': f'{product} is sold by weight; enter the kg.'})
        elif not product.is_catch_weight and quantity:
            raise serializers.ValidationError({'quantity': f'{product} is sold loose by weight; the count stays 0.'})
        elif not (quantity or weight):
            raise serializers.ValidationError({'weight_kg': 'Move some pieces or some kg.'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        """Inbound comes from SUPPLIER into the batch's location; outbound goes from it to ADJUSTMENT."""
        # Lock the batch so two concurrent outbound movements cannot both take the last units.
        batch = (StockBatch.objects.select_for_update(of=('self',)).select_related('location')
                 .get(pk=validated_data['batch'].pk))
        quantity, movement_type = validated_data['quantity'], validated_data['movement_type']
        weight = validated_data.get('weight_kg')
        if movement_type == 'outbound' and quantity > batch.quantity:
            raise serializers.ValidationError(
                {'quantity': f"Only {batch.quantity} unit(s) left in batch {batch.batch_number}."})
        if movement_type == 'outbound' and weight is not None and weight > batch.weight_kg:
            raise serializers.ValidationError(
                {'weight_kg': f'Only {batch.weight_kg} kg left in batch {batch.batch_number}.'})
        if (movement_type == 'outbound' and batch.product.is_catch_weight and quantity
                and quantity == batch.quantity and weight != batch.weight_kg):
            # the last pieces take their kg with them, so no batch holds kg with no pieces
            raise serializers.ValidationError(
                {'weight_kg': f'These are the last pieces of batch {batch.batch_number}: move all {batch.weight_kg} kg.'})
        counterpart = virtual_location(
            batch.location.company_id, Location.SUPPLIER if movement_type == 'inbound' else Location.ADJUSTMENT
        )
        return move(batch, quantity, movement_type, counterpart=counterpart, weight_kg=weight)


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
        fields = ['id', 'product', 'quantity', 'received_quantity', 'difference_reason', 'requisition_line']
        read_only_fields = ['id', 'received_quantity', 'difference_reason', 'requisition_line']
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
        if 'company' not in validated_data:
            raise serializers.ValidationError({'detail': ['Your account is not linked to a company.']})
        try:
            return transfers.create_transfer(
                company=validated_data['company'], user=self.context['request'].user,
                source=validated_data['source_location'], destination=validated_data['destination_location'],
                lines=self._lines(validated_data['lines']), note=validated_data.get('note', ''),
            )
        except transfers.TransferError as error:
            raise serializers.ValidationError(error.detail)

    def update(self, instance, validated_data):
        lines = self._lines(validated_data['lines']) if 'lines' in validated_data else None
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


class RequisitionLineSerializer(TenantModelSerializer):
    fulfilled_quantity = serializers.IntegerField(read_only=True, help_text='Units on transfers (not cancelled ones).')
    open_quantity = serializers.IntegerField(read_only=True, help_text='Units not yet on a transfer.')

    class Meta:
        model = RequisitionLine
        fields = ['id', 'product', 'quantity', 'fulfilled_quantity', 'open_quantity']
        read_only_fields = ['id']
        extra_kwargs = {'quantity': {'min_value': 1}}


class RequisitionSerializer(TenantModelSerializer):
    """
    A store location asking for stock (``location``). The warehouse fulfils it with ``fulfil`` (a draft transfer
    per call, in full or in parts); what is not on a transfer stays open. Numbers are per company (R-000001).
    """

    reference = serializers.CharField(read_only=True)
    lines = RequisitionLineSerializer(many=True)
    transfers = serializers.SerializerMethodField(help_text='Ids of the transfers made for it, oldest first.')

    class Meta:
        model = Requisition
        fields = ['id', 'number', 'reference', 'location', 'status', 'note', 'lines', 'transfers', 'created_by',
                  'created_at', 'cancelled_by', 'cancelled_at']
        read_only_fields = ['id', 'number', 'status', 'created_by', 'created_at', 'cancelled_by', 'cancelled_at']

    def get_transfers(self, requisition) -> list[int]:
        return list(requisition.transfers().values_list('pk', flat=True))

    def create(self, validated_data):
        if 'company' not in validated_data:
            raise serializers.ValidationError({'detail': ['Your account is not linked to a company.']})
        try:
            return requisitions.create_requisition(
                company=validated_data['company'], user=self.context['request'].user,
                location=validated_data['location'], note=validated_data.get('note', ''),
                lines=[(line['product'], line['quantity']) for line in validated_data['lines']],
            )
        except requisitions.RequisitionError as error:
            raise serializers.ValidationError(error.detail)


class RequisitionFulfilLineSerializer(serializers.Serializer):
    line = serializers.IntegerField(help_text='A line id of this requisition.')
    quantity = serializers.IntegerField(min_value=0, help_text='Units to send now (at most what is open).')


class RequisitionFulfilSerializer(TenantModelSerializer):
    """Where the stock comes from and how much of each line to send now; lines left out stay open."""

    lines = RequisitionFulfilLineSerializer(many=True)

    class Meta:
        model = Transfer
        fields = ['source_location', 'lines']


class BarcodeRuleSerializer(TenantModelSerializer):
    """A scale-label layout. ``value_length`` is what is left of the 13 digits for the price or weight."""

    value_length = serializers.SerializerMethodField()

    class Meta:
        model = BarcodeRule
        fields = ['id', 'name', 'prefix', 'item_code_length', 'value_type', 'decimals', 'value_check_digit',
                  'value_length', 'is_active']

    def get_value_length(self, rule) -> int:
        return rule.to_rule().value_length

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # Fields left out keep the instance's value, or the model default on create.
        merged = {name: attrs[name] if name in attrs else getattr(
                      self.instance, name, BarcodeRule._meta.get_field(name).get_default())
                  for name in ('prefix', 'item_code_length', 'value_type', 'decimals', 'value_check_digit')}
        error = BarcodeRule(**merged).to_rule().layout_error()
        if error:
            field, message = error.split(': ', 1)
            raise serializers.ValidationError({field: message})
        company = self.context['request'].user.company
        taken = BarcodeRule.objects.filter(company=company, prefix=merged['prefix'])
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise serializers.ValidationError({'prefix': 'Another rule already uses this prefix.'})
        return attrs


class YieldTemplateOutputSerializer(TenantModelSerializer):
    # Declared so a left-out kind reaches validation and saving as a product (the model default).
    kind = serializers.ChoiceField(choices=YieldTemplateOutput.KINDS, default=YieldTemplateOutput.PRODUCT)
    name = serializers.CharField(max_length=100, required=False, allow_blank=True,
                                 help_text='The waste, e.g. Bone. Product outputs show the product name.')

    class Meta:
        model = YieldTemplateOutput
        fields = ['id', 'kind', 'product', 'name', 'expected_percent', 'keeps_days']
        read_only_fields = ['id']

    def to_representation(self, output):
        data = super().to_representation(output)
        data['name'] = output.label
        return data


PERCENT = serializers.DecimalField(max_digits=5, decimal_places=2)


class YieldTemplateSerializer(TenantModelSerializer):
    """
    A butchery yield template (G20): the input product and what it breaks down into, each an expected % of the
    input's weight. Outputs total at most 100; ``expected_loss_percent`` is the rest. Sending ``outputs`` replaces
    them all.
    """

    input_product_name = serializers.CharField(source='input_product.name', read_only=True)
    outputs = YieldTemplateOutputSerializer(many=True)
    expected_yield_percent = serializers.SerializerMethodField(help_text='Product outputs, % of the input weight.')
    expected_waste_percent = serializers.SerializerMethodField(help_text='Waste outputs, % of the input weight.')
    expected_loss_percent = serializers.SerializerMethodField(help_text='100 minus outputs and waste.')

    class Meta:
        model = YieldTemplate
        fields = ['id', 'name', 'input_product', 'input_product_name', 'is_active', 'note', 'outputs',
                  'expected_yield_percent', 'expected_waste_percent', 'expected_loss_percent', 'created_at']
        read_only_fields = ['id', 'created_at']

    def _totals(self, template):
        return YieldTemplate.percent_totals(list(template.outputs.all()))

    @extend_schema_field(PERCENT)
    def get_expected_yield_percent(self, template):
        return f'{self._totals(template)[0]:.2f}'

    @extend_schema_field(PERCENT)
    def get_expected_waste_percent(self, template):
        return f'{self._totals(template)[1]:.2f}'

    @extend_schema_field(PERCENT)
    def get_expected_loss_percent(self, template):
        return f'{self._totals(template)[2]:.2f}'

    def validate_input_product(self, product):
        problem = weighed_product_problem(product)
        if problem:
            raise serializers.ValidationError(problem)
        return product

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if 'outputs' in attrs:
            input_product = attrs.get('input_product', getattr(self.instance, 'input_product', None))
            problems = yield_output_problems(input_product, attrs['outputs'])
            if problems:
                raise serializers.ValidationError({'outputs': problems})
        elif 'input_product' in attrs and self.instance is not None:  # a new input against the outputs kept
            kept = [{'kind': o.kind, 'product': o.product, 'name': o.name, 'expected_percent': o.expected_percent,
                     'keeps_days': o.keeps_days}
                    for o in self.instance.outputs.all()]
            problems = yield_output_problems(attrs['input_product'], kept)
            if problems:
                raise serializers.ValidationError({'outputs': problems})
        return attrs

    @staticmethod
    def _save_outputs(template, outputs):
        template.outputs.all().delete()
        YieldTemplateOutput.objects.bulk_create(
            YieldTemplateOutput(template=template, position=index, kind=output['kind'], product=output.get('product'),
                                name='' if output['kind'] == YieldTemplateOutput.PRODUCT else output['name'].strip(),
                                expected_percent=output['expected_percent'], keeps_days=output.get('keeps_days'))
            for index, output in enumerate(outputs)
        )

    def create(self, validated_data):
        outputs = validated_data.pop('outputs')
        with transaction.atomic():
            template = super().create(validated_data)
            self._save_outputs(template, outputs)
        return template

    def update(self, instance, validated_data):
        outputs = validated_data.pop('outputs', None)
        with transaction.atomic():
            template = super().update(instance, validated_data)
            if outputs is not None:
                self._save_outputs(template, outputs)
        return template


class BreakdownLineSerializer(TenantModelSerializer):
    """What came out for one template output, and the kg the template expected for this input."""

    output = serializers.PrimaryKeyRelatedField(source='template_output', read_only=True)
    expected_kg = serializers.SerializerMethodField(help_text="The template's expected % of the input kg.")

    class Meta:
        model = BreakdownLine
        fields = ['id', 'output', 'kind', 'product', 'name', 'weight_kg', 'pieces', 'expiration_date', 'output_batch',
                  'expected_kg']
        read_only_fields = fields

    @extend_schema_field(serializers.DecimalField(max_digits=10, decimal_places=3))
    def get_expected_kg(self, line):
        return f'{_kg_share(line.breakdown.input_kg, line.template_output.expected_percent)}'


def _kg_share(kg, percent):
    return (kg * percent / 100).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)


class BreakdownSerializer(TenantModelSerializer):
    """A recorded butchery breakdown (G21): input = product kg + waste kg + ``loss_kg`` exactly."""

    reference = serializers.CharField(read_only=True)
    template_name = serializers.CharField(source='template.name', read_only=True)
    input_batch_number = serializers.CharField(source='input_batch.batch_number', read_only=True)
    input_product_name = serializers.CharField(source='input_batch.product.name', read_only=True)
    expected_loss_kg = serializers.SerializerMethodField(help_text="The template's expected loss for this input.")
    lines = BreakdownLineSerializer(many=True, read_only=True)

    class Meta:
        model = Breakdown
        fields = ['id', 'number', 'reference', 'template', 'template_name', 'location', 'input_batch',
                  'input_batch_number', 'input_product_name', 'input_pieces', 'input_kg', 'loss_kg', 'loss_reason',
                  'expected_loss_kg', 'lines', 'status', 'client_ref', 'created_by', 'created_at', 'voided_by',
                  'voided_at', 'void_reason']
        read_only_fields = fields

    @extend_schema_field(serializers.DecimalField(max_digits=10, decimal_places=3))
    def get_expected_loss_kg(self, breakdown):
        return f'{_kg_share(breakdown.input_kg, breakdown.template.expected_loss_percent)}'


class BreakdownLineInputSerializer(TenantModelSerializer):
    output = serializers.PrimaryKeyRelatedField(source='template_output', queryset=YieldTemplateOutput.objects.all(),
                                                help_text="A template output's id.")
    pieces = serializers.IntegerField(min_value=0, required=False, default=0,
                                      help_text='Catch-weight output products only.')
    expiration_date = serializers.DateField(required=False, allow_null=True, default=None,
                                            help_text='Earlier than the template allows, if needed.')

    class Meta:
        model = BreakdownLine
        fields = ['output', 'weight_kg', 'pieces', 'expiration_date']


class BreakdownCreateSerializer(TenantModelSerializer):
    """
    Record a breakdown once the cutting is done. Loose inputs send ``input_kg``, catch-weight ones ``input_pieces``.
    Outputs left out of ``lines`` produced nothing. A repeated ``client_ref`` returns the first breakdown.
    """

    input_kg = serializers.DecimalField(max_digits=10, decimal_places=3, required=False, allow_null=True)
    input_pieces = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    lines = BreakdownLineInputSerializer(many=True, max_length=100)
    loss_reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    client_ref = serializers.UUIDField(required=False, allow_null=True, default=None)

    class Meta:
        model = Breakdown
        fields = ['template', 'location', 'input_batch', 'input_kg', 'input_pieces', 'lines', 'loss_reason',
                  'client_ref']


class BreakdownVoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, help_text='Why the breakdown is being reversed.')
