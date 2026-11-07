from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from common.tenancy import TenantModelSerializer
from inventory.models import Location, Product

from .models import (
    WEEKDAYS,
    CustomerGroup,
    Invoice,
    InvoiceLine,
    PriceList,
    PriceListEntry,
    SalesOrder,
    SalesOrderLine,
    SalesOrderPick,
    TradeCustomer,
    TradeCustomerAddress,
    TradeCustomerContact,
    account_code_problem,
    dates_problem,
    delivery_days_problem,
    in_week_order,
    min_quantity_problem,
    normalise_account_code,
    normalise_postcode,
    normalise_vat_number,
    postcode_problem,
    vat_number_problem,
)


class TradeCustomerAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = TradeCustomerAddress
        fields = ['id', 'label', 'line1', 'line2', 'town', 'postcode', 'instructions', 'is_default']
        read_only_fields = ['id']

    def to_internal_value(self, data):
        if hasattr(data, 'copy') and isinstance(data.get('postcode'), str):  # before the length check
            data = data.copy()
            data['postcode'] = normalise_postcode(data['postcode'])
        return super().to_internal_value(data)

    def validate_postcode(self, value):
        value = normalise_postcode(value)
        problem = postcode_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return value


class TradeCustomerContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = TradeCustomerContact
        fields = ['id', 'name', 'role', 'email', 'phone', 'is_primary']
        read_only_fields = ['id']


class TradeCustomerSerializer(TenantModelSerializer):
    """
    A trade customer (G38) with its delivery addresses and contacts. Sending ``addresses`` or ``contacts`` replaces
    them all; leaving one out keeps it. At most one default address and one primary contact (a single one becomes
    it).
    """

    delivery_days = serializers.ListField(
        child=serializers.ChoiceField(choices=WEEKDAYS), required=False,
        help_text='Days it takes deliveries: mon, tue, ... sun, each once.',
    )
    addresses = TradeCustomerAddressSerializer(many=True, required=False)
    contacts = TradeCustomerContactSerializer(many=True, required=False)

    class Meta:
        model = TradeCustomer
        fields = [
            'id', 'account_code', 'legal_name', 'trading_name', 'vat_number', 'status', 'delivery_days', 'note',
            'group', 'invoice_email', 'payment_terms_days', 'addresses', 'contacts', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def to_internal_value(self, data):
        # Normalise before the unique (company, account_code) check sees it.
        if hasattr(data, 'copy') and hasattr(data, 'get'):  # a dict, or a QueryDict from a form post
            data = data.copy()
            if isinstance(data.get('account_code'), str):
                data['account_code'] = normalise_account_code(data['account_code'])
            if isinstance(data.get('vat_number'), str):  # before the length check: people type spaces
                data['vat_number'] = normalise_vat_number(data['vat_number'])
        return super().to_internal_value(data)

    def validate_account_code(self, value):
        problem = account_code_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return value

    def validate_vat_number(self, value):
        value = normalise_vat_number(value)
        problem = vat_number_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return value

    def validate_delivery_days(self, value):
        problem = delivery_days_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return in_week_order(value)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        group, company_id = attrs.get('group'), self._company_id_for(attrs)
        if group is not None and company_id is not None and group.company_id != company_id:
            raise serializers.ValidationError({'group': 'This group belongs to another company.'})
        return attrs

    def validate_addresses(self, value):
        if sum(bool(a.get('is_default')) for a in value) > 1:
            raise serializers.ValidationError('Only one default address.')
        if len(value) == 1:
            value[0]['is_default'] = True
        return value

    def validate_contacts(self, value):
        if sum(bool(c.get('is_primary')) for c in value) > 1:
            raise serializers.ValidationError('Only one primary contact.')
        if len(value) == 1:
            value[0]['is_primary'] = True
        return value

    @staticmethod
    def _replace(customer, addresses, contacts):
        if addresses is not None:
            customer.addresses.all().delete()
            TradeCustomerAddress.objects.bulk_create(TradeCustomerAddress(customer=customer, **a) for a in addresses)
        if contacts is not None:
            customer.contacts.all().delete()
            TradeCustomerContact.objects.bulk_create(TradeCustomerContact(customer=customer, **c) for c in contacts)

    def create(self, validated_data):
        addresses = validated_data.pop('addresses', [])
        contacts = validated_data.pop('contacts', [])
        with transaction.atomic():
            customer = super().create(validated_data)
            self._replace(customer, addresses, contacts)
        return customer

    def update(self, instance, validated_data):
        addresses = validated_data.pop('addresses', None)
        contacts = validated_data.pop('contacts', None)
        with transaction.atomic():
            customer = super().update(instance, validated_data)
            self._replace(customer, addresses, contacts)
        return customer


class CustomerGroupSerializer(TenantModelSerializer):
    """Trade customers priced alike (G39). Deleting a group leaves its customers with no group."""

    class Meta:
        model = CustomerGroup
        fields = ['id', 'name', 'note']
        read_only_fields = ['id']


class PriceListEntrySerializer(TenantModelSerializer):
    """A price break: from ``min_quantity`` (units, or kg for weighed products) the whole line costs ``unit_price``
    ex VAT (per kg for weighed products)."""

    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = PriceListEntry
        fields = ['id', 'product', 'product_name', 'min_quantity', 'unit_price']
        read_only_fields = ['id']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        problem = min_quantity_problem(attrs['product'], attrs.get('min_quantity', PriceListEntry._meta.get_field(
            'min_quantity').get_default()))
        if problem:
            raise serializers.ValidationError({'min_quantity': problem})
        return attrs


class PriceListSerializer(TenantModelSerializer):
    """
    A price list (G39), ex VAT, for the customers and groups it is assigned to (none: a draft). Sending
    ``entries`` replaces them all; leaving it out keeps them. Valid on UK dates, inclusive; blank is open-ended.
    """

    entries = PriceListEntrySerializer(many=True, required=False)

    class Meta:
        model = PriceList
        fields = ['id', 'name', 'priority', 'is_active', 'valid_from', 'valid_to', 'customers', 'groups', 'note',
                  'entries', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_entries(self, value):
        seen = set()
        for entry in value:
            key = (entry['product'].pk, entry.get('min_quantity', PriceListEntry._meta.get_field(
                'min_quantity').get_default()))
            if key in seen:
                raise serializers.ValidationError('Each product has one price per minimum quantity.')
            seen.add(key)
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)

        def value(name):
            return attrs[name] if name in attrs else getattr(self.instance, name, None)

        problem = dates_problem(value('valid_from'), value('valid_to'))
        if problem:
            raise serializers.ValidationError({'valid_to': problem})
        # Superusers' choices are not scoped: still, a list only links rows of its own company.
        company_id = self._company_id_for(attrs)
        for name, rows in (('customers', attrs.get('customers', [])), ('groups', attrs.get('groups', [])),
                           ('entries', [e['product'] for e in attrs.get('entries', [])])):
            if company_id is not None and any(row.company_id != company_id for row in rows):
                raise serializers.ValidationError({name: "Another company's rows can't be on this list."})
        return attrs

    @staticmethod
    def _replace(price_list, entries):
        if entries is not None:
            price_list.entries.all().delete()
            PriceListEntry.objects.bulk_create(PriceListEntry(price_list=price_list, **e) for e in entries)

    def create(self, validated_data):
        entries = validated_data.pop('entries', [])
        with transaction.atomic():
            price_list = super().create(validated_data)
            self._replace(price_list, entries)
        return price_list

    def update(self, instance, validated_data):
        entries = validated_data.pop('entries', None)
        with transaction.atomic():
            price_list = super().update(instance, validated_data)
            self._replace(price_list, entries)
        return price_list


MAX_RESOLVE_LINES = 500


class _OwnCompanyPrimaryKey(serializers.PrimaryKeyRelatedField):
    """Only the requesting user's company's rows, superusers included (another company's id "does not exist")."""

    def get_queryset(self):
        return super().get_queryset().filter(company_id=self.context['request'].user.company_id)


class ResolveLineSerializer(serializers.Serializer):
    product = serializers.IntegerField(min_value=1, help_text='A product of your company.')
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, help_text='Units, or kg when sold by weight.')


class ResolveRequestSerializer(serializers.Serializer):
    customer = _OwnCompanyPrimaryKey(queryset=TradeCustomer.objects.all())
    on = serializers.DateField(required=False, help_text='UK date; today when left out.')
    lines = ResolveLineSerializer(many=True, allow_empty=False, max_length=MAX_RESOLVE_LINES)

    def validate_lines(self, lines):
        # One query for all the products (not one per line), the user's own company's only.
        products = Product.objects.filter(company_id=self.context['request'].user.company_id).in_bulk(
            {line['product'] for line in lines})
        errors, found = [], []
        for line in lines:
            product = products.get(line['product'])
            if product is None:
                errors.append({'product': f'Invalid pk "{line["product"]}" - object does not exist.'})
                continue
            problem = min_quantity_problem(product, line['quantity'])
            errors.append({'quantity': problem} if problem else {})
            found.append({'product': product, 'quantity': line['quantity']})
        if any(errors):
            raise serializers.ValidationError(errors)
        return found


class ResolvedLineSerializer(serializers.Serializer):
    """A priced line; or, with no trade price, just ``product``, ``quantity`` and ``error`` "no_price"."""

    product = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=4, required=False,
                                          help_text='Ex VAT, per unit (per kg when sold by weight).')
    source = serializers.ChoiceField(choices=['customer_list', 'group_list', 'base'], required=False)
    price_list = serializers.IntegerField(allow_null=True, required=False)
    price_list_name = serializers.CharField(allow_null=True, required=False)
    min_quantity = serializers.DecimalField(max_digits=12, decimal_places=3, allow_null=True, required=False)
    error = serializers.ChoiceField(choices=['no_price'], required=False)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if 'error' in instance:  # nothing priced it: only what was asked, and why
            return {name: data[name] for name in ('product', 'quantity', 'error')}
        return data


class ResolveResponseSerializer(serializers.Serializer):
    customer = serializers.IntegerField()
    on = serializers.DateField()
    lines = ResolvedLineSerializer(many=True)


MAX_ORDER_UNITS = 100_000
MAX_ORDER_KG = Decimal('10000')
HOLDING_STATUSES = (SalesOrder.CONFIRMED, SalesOrder.PICKING)
PICK_FIELDS = ['picks', 'picked_quantity', 'picked_weight_kg', 'substitute_for', 'substitution', 'proposed_by',
               'decided_by', 'decided_at']
ORDER_SNAPSHOT_FIELDS = ['unit_price', 'price_source', 'price_list', 'price_list_name', 'min_quantity', 'vat_code',
                         'vat_rate', 'estimated_net', 'estimated_vat']


class SalesOrderPickSerializer(serializers.ModelSerializer):
    """What was picked for a line from one warehouse lot (G42)."""

    batch_number = serializers.CharField(source='batch.batch_number', read_only=True)
    expiration_date = serializers.DateField(source='batch.expiration_date', read_only=True)

    class Meta:
        model = SalesOrderPick
        fields = ['id', 'line', 'batch', 'batch_number', 'expiration_date', 'quantity', 'weight_kg', 'barcode',
                  'picked_by', 'picked_at']
        read_only_fields = fields


class SalesOrderLineSerializer(TenantModelSerializer):
    """
    A product on an order: ``quantity`` units for each products and pieces for catch-weight ones, or ``weight_kg``
    for loose weighed products. The price fields are filled when the order is confirmed (ex VAT, per unit or per
    kg); for catch-weight, ``weight_kg`` is then the estimate (pieces x nominal weight).
    """

    product_name = serializers.CharField(source='product.name', read_only=True)
    reserved = serializers.SerializerMethodField(
        help_text='Held at the warehouse (G41): units, or kg for loose products; null unless confirmed or picking.')
    short = serializers.SerializerMethodField(help_text='Still to reserve; null unless confirmed or picking.')
    zone = serializers.CharField(source='product.pick_zone', read_only=True, help_text='Its place on the pick list.')
    picks = SalesOrderPickSerializer(many=True, read_only=True)
    # Trade-sized caps, well inside the columns (and the 12-digit estimated totals).
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_ORDER_UNITS, required=False, allow_null=True)
    weight_kg = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal('0.001'),
                                         max_value=MAX_ORDER_KG, required=False, allow_null=True)

    class Meta:
        model = SalesOrderLine
        fields = ['id', 'product', 'product_name', 'quantity', 'weight_kg', 'note', 'reserved', 'short', 'zone',
                  *ORDER_SNAPSHOT_FIELDS, *PICK_FIELDS]
        read_only_fields = ['id', *ORDER_SNAPSHOT_FIELDS, *PICK_FIELDS]

    @staticmethod
    def amounts(line):
        """``(reserved, short)`` while the order holds stock, else ``(None, None)``."""
        if line.order.status not in HOLDING_STATUSES:
            return None, None
        loose = line.product.is_weighed and not line.product.is_catch_weight
        rows = getattr(line, 'active_reservations', None)
        if rows is None:
            rows = line.reservations.filter(released_at__isnull=True)
        if loose:
            reserved = sum((row.weight_kg or Decimal('0') for row in rows), Decimal('0')).quantize(Decimal('0.001'))
            return reserved, max(line.weight_kg - reserved, Decimal('0.000'))
        reserved = sum(row.quantity for row in rows)
        return reserved, max(line.quantity - reserved, 0)

    def get_reserved(self, line) -> str | None:
        reserved, _short = self.amounts(line)
        return None if reserved is None else str(reserved)

    def get_short(self, line) -> str | None:
        _reserved, short = self.amounts(line)
        return None if short is None else str(short)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if 'product' not in attrs:  # a PATCH passes "partial" down, but a sent line is always a whole line
            raise serializers.ValidationError({'product': 'This field is required.'})
        product, quantity, weight = attrs['product'], attrs.get('quantity'), attrs.get('weight_kg')
        if product.is_weighed and not product.is_catch_weight:
            if weight is None:
                raise serializers.ValidationError({'weight_kg': 'Sold loose by weight: give the kg.'})
            if quantity is not None:
                raise serializers.ValidationError({'quantity': 'Sold loose by weight: kg only.'})
        else:
            if quantity is None:
                raise serializers.ValidationError({'quantity': 'Give the number of pieces.' if product.is_weighed
                                                   else 'Give the number of units.'})
            if weight is not None:
                raise serializers.ValidationError({'weight_kg': 'Ordered by count; the kg is worked out later.'})
        return attrs


class SalesOrderSerializer(TenantModelSerializer):
    """
    A trade sales order (G40). On create, ``address`` picks one of the customer's addresses (the default when left
    out); it is copied onto the order. Only a draft can be changed; sending ``lines`` replaces them all, and sending
    ``address`` copies it again. Prices and estimated totals are fixed when the order is confirmed.
    """

    number_display = serializers.CharField(read_only=True)
    customer_code = serializers.CharField(source='customer.account_code', read_only=True)
    customer_name = serializers.SerializerMethodField()
    off_delivery_day = serializers.BooleanField(read_only=True,
                                                help_text="Delivered on a day the customer doesn't take deliveries.")
    is_short = serializers.SerializerMethodField(help_text='A line could not be fully reserved (G41).')
    address = serializers.PrimaryKeyRelatedField(queryset=TradeCustomerAddress.objects.all(), write_only=True,
                                                 required=False, help_text="One of the customer's addresses.")
    lines = SalesOrderLineSerializer(many=True, allow_empty=False)

    class Meta:
        model = SalesOrder
        fields = [
            'id', 'number', 'number_display', 'customer', 'customer_code', 'customer_name', 'delivery_date',
            'off_delivery_day', 'customer_ref', 'note', 'status', 'source_location', 'is_short', 'address', 'delivery_label', 'line1', 'line2',
            'town', 'postcode', 'instructions', 'lines', 'estimated_net', 'estimated_vat', 'estimated_gross',
            'created_by', 'confirmed_by', 'confirmed_at', 'cancelled_at', 'cancel_reason', 'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'number', 'status', 'delivery_label', 'line1', 'line2', 'town', 'postcode', 'instructions',
            'estimated_net', 'estimated_vat', 'estimated_gross', 'created_by', 'confirmed_by', 'confirmed_at',
            'cancelled_at', 'cancel_reason', 'created_at', 'updated_at',
        ]

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:
            fields['customer'].read_only = True  # its address and prices belong to it
            fields['lines'].required = False
        return fields

    def get_customer_name(self, order) -> str:
        return order.customer.trading_name or order.customer.legal_name

    def get_is_short(self, order) -> bool:
        return any((SalesOrderLineSerializer.amounts(line)[1] or 0) > 0 for line in order.lines.all())

    def validate_source_location(self, location):
        if location is not None and (location.type != Location.WAREHOUSE or not location.is_active):
            raise serializers.ValidationError('Choose an active warehouse.')
        return location

    def validate(self, attrs):
        attrs = super().validate(attrs)
        company_id = self._company_id_for(attrs)
        customer = attrs.get('customer') or getattr(self.instance, 'customer', None)
        # Superusers' choices are not scoped: still, an order only links rows of its own company.
        if customer.company_id != company_id:
            raise serializers.ValidationError({'customer': 'This customer belongs to another company.'})
        if self.instance is None and customer.status == TradeCustomer.CLOSED:
            raise serializers.ValidationError({'customer': "A closed customer can't get new orders."})
        source = attrs.get('source_location')
        if source is not None and source.company_id != company_id:
            raise serializers.ValidationError({'source_location': 'This location belongs to another company.'})
        if self.instance is None and 'source_location' not in attrs:
            warehouses = list(Location.objects.filter(company_id=company_id, type=Location.WAREHOUSE,
                                                      is_active=True).values_list('pk', flat=True)[:2])
            if len(warehouses) > 1:
                raise serializers.ValidationError({'source_location': 'Choose the warehouse it is picked from.'})
            attrs['source_location_id'] = warehouses[0] if warehouses else None
        address = attrs.get('address')
        if address is not None and address.customer_id != customer.pk:
            raise serializers.ValidationError({'address': "Not one of this customer's addresses."})
        if address is None and self.instance is None:
            address = customer.addresses.filter(is_default=True).first()
            if address is None:
                raise serializers.ValidationError({'address': 'The customer has no default address; choose one.'})
            attrs['address'] = address
        lines = attrs.get('lines', [])
        if any(line['product'].company_id != company_id for line in lines):
            raise serializers.ValidationError({'lines': "Another company's products can't be ordered."})
        products = [line['product'].pk for line in lines]
        if len(set(products)) != len(products):
            raise serializers.ValidationError({'lines': 'Each product once per order.'})
        return attrs

    @staticmethod
    def _copy_address(order, address):
        order.delivery_label, order.line1, order.line2 = address.label, address.line1, address.line2
        order.town, order.postcode, order.instructions = address.town, address.postcode, address.instructions

    @staticmethod
    def _replace_lines(order, lines):
        order.lines.all().delete()
        SalesOrderLine.objects.bulk_create(SalesOrderLine(order=order, **line) for line in lines)

    def create(self, validated_data):
        from .orders import next_order_number

        lines = validated_data.pop('lines')
        address = validated_data.pop('address')
        request = self.context['request']
        with transaction.atomic():
            order = SalesOrder(**validated_data, company_id=self._company_id_for(validated_data),
                               created_by=request.user)
            order.number = next_order_number(order.company)
            self._copy_address(order, address)
            order.save()
            self._replace_lines(order, lines)
        return order

    def update(self, instance, validated_data):
        lines = validated_data.pop('lines', None)
        address = validated_data.pop('address', None)
        with transaction.atomic():
            status = SalesOrder.objects.select_for_update().values_list('status', flat=True).get(pk=instance.pk)
            if status != SalesOrder.DRAFT:
                raise serializers.ValidationError({'detail': 'Only a draft order can be changed.'})
            if address is not None:
                self._copy_address(instance, address)
            order = super().update(instance, validated_data)
            if lines is not None:
                self._replace_lines(order, lines)
        return order


class CancelOrderSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, help_text='Why the order is cancelled.')


class PickRequestSerializer(serializers.Serializer):
    """One pick (G42): the line, and the lot's number or a scanned scale/GS1 label (one item, with its kg)."""

    line = serializers.IntegerField(min_value=1)
    batch_number = serializers.CharField(max_length=50, required=False, allow_blank=False)
    barcode = serializers.CharField(max_length=64, required=False, allow_blank=False)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_ORDER_UNITS, required=False)
    weight_kg = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal('0.001'),
                                         max_value=MAX_ORDER_KG, required=False)

    def validate(self, attrs):
        if bool(attrs.get('batch_number')) == bool(attrs.get('barcode')):
            raise serializers.ValidationError('Send the lot number or a label, not both.')
        return attrs


class SubstituteRequestSerializer(serializers.Serializer):
    """A swap proposed for a line (G42): another product of the company, in units, pieces or kg (loose)."""

    line = serializers.IntegerField(min_value=1)
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_ORDER_UNITS, required=False)
    weight_kg = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal('0.001'),
                                         max_value=MAX_ORDER_KG, required=False)

    def validate_product(self, product):
        if product.company_id != self.context['request'].user.company_id:  # superusers included
            raise serializers.ValidationError("Another company's product can't be sent.")
        return product


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = ['id', 'order_line', 'product', 'description', 'quantity', 'weight_kg', 'unit_price', 'vat_code',
                  'vat_rate', 'net', 'vat', 'gross']
        read_only_fields = fields


class VatSummaryRowSerializer(serializers.Serializer):
    rate = serializers.DecimalField(max_digits=5, decimal_places=2)
    net = serializers.DecimalField(max_digits=12, decimal_places=2)
    vat = serializers.DecimalField(max_digits=12, decimal_places=2)


class InvoiceSerializer(serializers.ModelSerializer):
    """
    A trade invoice (G43). Created as a draft from a picked order (``order``), billed on what was picked; issuing
    numbers and freezes it. Nothing here is edited: drafts are rebuilt (``refresh``) or deleted, issued invoices are
    corrected by credit notes (G45).
    """

    number_display = serializers.CharField(read_only=True)
    order_number = serializers.CharField(source='order.number_display', read_only=True)
    customer = serializers.IntegerField(source='order.customer_id', read_only=True)
    lines = InvoiceLineSerializer(many=True, read_only=True)
    vat_summary = VatSummaryRowSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            'id', 'order', 'order_number', 'customer', 'status', 'number', 'number_display', 'invoice_date',
            'due_date', 'seller_name', 'seller_address', 'seller_vat_number', 'customer_code', 'customer_name',
            'customer_vat_number', 'customer_ref', 'address_line1', 'address_line2', 'address_town',
            'address_postcode', 'lines', 'net_total', 'vat_total', 'gross_total', 'vat_summary', 'created_by',
            'created_at', 'issued_by', 'issued_at', 'emailed_to', 'emailed_at', 'email_status',
        ]
        read_only_fields = [name for name in fields if name != 'order']

    def validate_order(self, order):
        if order.company_id != self.context['request'].user.company_id:  # superusers included
            raise serializers.ValidationError("Another company's order can't be invoiced here.")
        return order
