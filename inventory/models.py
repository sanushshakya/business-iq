"""
inventory/models.py

This module contains Django models for the inventory app. These models are responsible
for representing and managing inventory-related data within the application.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from vat.models import VAT_CODES, ZERO

class ProductCategory(models.Model):
    """
    A category used to group products (e.g. for demand-calendar events).
    """
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name_plural = 'product categories'

    def __str__(self):
        return self.name


def normalise_plu(value):
    """A PLU as stored: 1 to 8 digits without leading zeros; raises ``ValidationError`` otherwise."""
    if not (value.isascii() and value.isdigit() and len(value) <= 8) or not value.strip('0'):
        raise ValidationError({'plu': 'A PLU is 1 to 8 digits (not all zeros).'})
    return value.lstrip('0')


class Product(models.Model):
    """
    Represents a product in the inventory.

    Attributes:
        name (str): The name of the product.
        description (str): A brief description of the product.
        price (float): The current price of the product.
        stock_quantity (int): The number of units currently in stock.
    """
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # Which UK VAT rate applies (vat.VatRate); the code decides, not the category. Till prices include VAT.
    vat_code = models.CharField(max_length=10, choices=VAT_CODES, default=ZERO)
    stock_quantity = models.IntegerField(default=0)
    reorder_threshold = models.PositiveIntegerField(default=5)
    commodity_code = models.CharField(max_length=20, blank=True)
    category = models.ForeignKey(
        ProductCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='products'
    )
    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='products')
    shopify_product_id = models.BigIntegerField(null=True, blank=True)
    # The id this product has in the system it was imported from (e.g. the online shop), so re-imports update it.
    external_id = models.CharField(max_length=64, blank=True)
    # Main pack barcode (EAN/UPC). Not unique: real catalogues share and reuse codes.
    barcode = models.CharField(max_length=32, blank=True, db_index=True)
    # Units kept back for the physical shop: the online storefront is offered in-date stock minus this.
    online_buffer = models.PositiveIntegerField(default=0, help_text='Keep for shop: units never offered online.')
    # Sold by weight: stock is kept in kg and till lines are priced weight × price_per_kg (``price`` is not used).
    # Catch-weight products are counted pieces that are each weighed; the others (loose) are kg only, count 0.
    EACH, WEIGHT = 'each', 'weight'
    SOLD_BY = [(EACH, 'Each'), (WEIGHT, 'By weight')]
    sold_by = models.CharField(max_length=10, choices=SOLD_BY, default=EACH)
    is_catch_weight = models.BooleanField(default=False, help_text='Weighed pieces (packs); only when sold by weight.')
    nominal_weight_kg = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True, help_text='A typical piece (catch-weight).'
    )
    price_per_kg = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    # The trade (B2B) base price ex VAT, per unit or per kg for weighed products (G39): used when no price list
    # prices the product. Commercial: staff only, never on till or storefront payloads.
    wholesale_price = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(Decimal('0'))],
        help_text='Trade price ex VAT (per kg when sold by weight), when no price list applies.',
    )
    # Where it is kept, for pick lists (G42); blank: butchery for weighed products, ambient otherwise.
    AMBIENT, CHILLED, FROZEN, BUTCHERY_ZONE = 'ambient', 'chilled', 'frozen', 'butchery'
    ZONES = [(AMBIENT, 'Ambient'), (CHILLED, 'Chilled'), (FROZEN, 'Frozen'), (BUTCHERY_ZONE, 'Butchery')]
    storage_zone = models.CharField(max_length=10, choices=ZONES, blank=True, default='',
                                    help_text='Where it is kept; sets its place on pick lists.')
    # The item number a scale prints on its labels (G18): digits, stored without leading zeros, unique per company.
    plu = models.CharField(max_length=8, blank=True, default='', help_text='Scale PLU (item number on labels).')
    # The till catalogue's version (G26): moves on every save and every stock movement, so tills fetch only changes.
    # Set in save() rather than auto_now, so loading a snapshot (loaddata, the seed command) still gets a value.
    updated_at = models.DateTimeField(default=timezone.now, db_index=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'external_id'], condition=~models.Q(external_id=''),
                name='unique_external_id_per_company',
            ),
            models.UniqueConstraint(fields=['company', 'plu'], condition=~models.Q(plu=''),
                                    name='unique_plu_per_company'),
        ]

    def clean(self):
        super().clean()
        if self.plu:
            self.plu = normalise_plu(self.plu)
            # The admin leaves company out of the form, so the unique constraint is not checked there.
            if self.company_id and Product.objects.filter(company_id=self.company_id, plu=self.plu).exclude(
                    pk=self.pk).exists():
                raise ValidationError({'plu': 'Another product already has this PLU.'})

    def save(self, *args, update_fields=None, **kwargs):
        self.updated_at = timezone.now()
        if update_fields is not None:  # a partial save (e.g. a price approval) is still a change tills must see
            update_fields = {*update_fields, 'updated_at'}
        super().save(*args, update_fields=update_fields, **kwargs)

    def __str__(self):
        return self.name

    @property
    def is_weighed(self):
        return self.sold_by == self.WEIGHT

    @property
    def pick_zone(self):
        return self.storage_zone or (self.BUTCHERY_ZONE if self.is_weighed else self.AMBIENT)


class ProductRemoval(models.Model):
    """A deleted product (G26), so tills holding the catalogue drop it on their next refresh."""

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='product_removals')
    product_id = models.PositiveBigIntegerField()
    removed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f'Product {self.product_id} removed'

class Order(models.Model):
    """
    Represents an order for a product.

    Attributes:
        product (Product): The product being ordered.
        quantity (int): The number of units ordered.
        ordered_at (datetime): The timestamp when the order was placed.
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.IntegerField(default=1)
    ordered_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} - Ordered {self.quantity}"

class Supplier(models.Model):
    """
    Represents a supplier for the products.

    Attributes:
        name (str): The name of the supplier.
        contact_info (str): Contact information for the supplier.
        address (str): Address of the supplier's warehouse or office.
    """
    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='suppliers')
    name = models.CharField(max_length=255)
    contact_info = models.TextField()
    address = models.TextField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'name'], name='unique_supplier_name_per_company')]

    def __str__(self):
        return self.name

class StockBatch(models.Model):
    """
    Represents a batch of stock for a product.

    Attributes:
        product (Product): The product associated with this stock batch.
        batch_number (str): Unique identifier for the batch.
        quantity (int): Number of units in the batch.
        receive_date (datetime): Date when the batch was received.
        expiration_date (datetime): Date when the batch expires.
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    batch_number = models.CharField(max_length=50)
    quantity = models.IntegerField(default=0)
    # Kg held, for products sold by weight (0 for each products). Loose products hold kg only.
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    receive_date = models.DateTimeField(auto_now_add=True)
    expiration_date = models.DateField()
    # Where this lot is. A lot split across places is one row per place (same number and expiry).
    location = models.ForeignKey('inventory.Location', on_delete=models.PROTECT, related_name='batches')
    # Landed cost ex VAT per kg (products sold by weight) or per unit; blank when not known. A breakdown spreads it
    # over its outputs (G22).
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True,
                                    validators=[MinValueValidator(Decimal('0'))])
    # Who it was received from (G24), for yield by supplier; breakdown outputs and transferred lots keep it.
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, null=True, blank=True, related_name='batches')

    class Meta:
        constraints = [
            # Per product (which belongs to one company), so companies never collide on batch numbers.
            models.UniqueConstraint(
                fields=['product', 'batch_number', 'location'], name='unique_batch_number_per_product_location'
            ),
        ]

    def __str__(self):
        return f"{self.product.name} - Batch {self.batch_number}"

class StockMovement(models.Model):
    """
    Represents a movement of stock, either inbound (receiving) or outbound (shipping).

    Attributes:
        batch (StockBatch): The batch of stock being moved.
        quantity (int): Number of units in the movement.
        movement_type (str): Type of movement, either 'inbound' or 'outbound'.
        date_time (datetime): Timestamp when the movement occurred.
    """
    batch = models.ForeignKey(StockBatch, on_delete=models.CASCADE)
    quantity = models.IntegerField(default=0)
    # Kg moved, for products sold by weight; empty for each products.
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    movement_type = models.CharField(max_length=10, choices=[('inbound', 'Inbound'), ('outbound', 'Outbound')])
    date_time = models.DateTimeField(auto_now_add=True)
    # Inbound: the batch's location is the destination; outbound: it is the source. The other side is where the
    # units came from or went to (a virtual location such as SUPPLIER or CUSTOMER, or another real one).
    source_location = models.ForeignKey('inventory.Location', on_delete=models.PROTECT, related_name='movements_out')
    destination_location = models.ForeignKey(
        'inventory.Location', on_delete=models.PROTECT, related_name='movements_in'
    )
    # Set on every movement a transfer writes (dispatch, receipt, shortfall).
    transfer = models.ForeignKey(
        'inventory.Transfer', null=True, blank=True, on_delete=models.PROTECT, related_name='movements'
    )
    breakdown = models.ForeignKey(
        'inventory.Breakdown', null=True, blank=True, on_delete=models.PROTECT, related_name='movements'
    )
    write_off = models.ForeignKey(
        'inventory.WriteOff', null=True, blank=True, on_delete=models.PROTECT, related_name='movements'
    )

    def __str__(self):
        return f"{self.batch.product.name} - {self.movement_type.capitalize()} - {self.quantity}"

class StockReservation(models.Model):
    """
    Stock held at a batch for a trade order line (G41) until it is picked: not a movement, so the batch keeps it.
    ``quantity`` is units (pieces for catch-weight; 0 for loose products) and ``weight_kg`` kg for loose products.
    Other FEFO takes skip reserved stock; a take of this very batch that leaves it holding less than its
    reservations trims them, newest first (``ledger.move``). Released and trimmed rows stay for the record.
    """

    batch = models.ForeignKey(StockBatch, on_delete=models.CASCADE, related_name='reservations')
    order_line = models.ForeignKey('wholesale.SalesOrderLine', on_delete=models.CASCADE, related_name='reservations')
    quantity = models.PositiveIntegerField(default=0)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['batch', 'created_at', 'id']
        indexes = [models.Index(fields=['batch', 'released_at'], name='reservation_batch_active')]

    def __str__(self):
        amount = self.quantity if self.weight_kg is None else f'{self.weight_kg} kg'
        return f'{amount} of {self.batch} reserved'


# Places outside the business, so every movement balances; one of each per company.
VIRTUAL_LOCATION_TYPES = ('in_transit', 'supplier', 'customer', 'waste', 'adjustment')


class Location(models.Model):
    """
    A place stock can be: a shop floor, a butchery counter, a warehouse, a van... or a virtual location for what
    is outside the business (supplier, customer, waste, adjustment, in transit), so every movement can balance.

    Virtual locations are created by the system (one of each per company) and never sellable. ``sellable`` means
    tills may sell from here; ``online_sellable`` (which needs ``sellable``) counts towards online stock.
    Retire a location with ``is_active`` once stock has moved through it.
    """

    SHOP_FLOOR, BUTCHERY, WAREHOUSE, BACK_STORE, VAN = 'shop_floor', 'butchery', 'warehouse', 'back_store', 'van'
    DISPATCH = 'dispatch'  # picked trade orders waiting for their delivery run (G42); never sellable
    IN_TRANSIT, SUPPLIER, CUSTOMER, WASTE, ADJUSTMENT = 'in_transit', 'supplier', 'customer', 'waste', 'adjustment'
    TYPES = [
        (SHOP_FLOOR, 'Shop floor'),
        (BUTCHERY, 'Butchery'),
        (WAREHOUSE, 'Warehouse'),
        (BACK_STORE, 'Back store'),
        (VAN, 'Van'),
        (DISPATCH, 'Dispatch'),
        (IN_TRANSIT, 'In transit'),
        (SUPPLIER, 'Supplier (virtual)'),
        (CUSTOMER, 'Customer (virtual)'),
        (WASTE, 'Waste (virtual)'),
        (ADJUSTMENT, 'Adjustment (virtual)'),
    ]
    VIRTUAL_TYPES = VIRTUAL_LOCATION_TYPES
    BRANCH_TYPES = (SHOP_FLOOR, BUTCHERY, BACK_STORE)  # always inside a branch

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='locations')
    # RESTRICT: a branch with locations can't be deleted on its own, but deleting the company removes both.
    branch = models.ForeignKey(
        'tenants.Branch', null=True, blank=True, on_delete=models.RESTRICT, related_name='locations'
    )
    code = models.CharField(
        max_length=20,
        validators=[RegexValidator(r'^[A-Z0-9][A-Z0-9-]*$', 'Use capital letters, digits and dashes.')],
        help_text='Short code, unique within the company (e.g. SHOP-1, WH-MAIN).',
    )
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=20, choices=TYPES)
    sellable = models.BooleanField(default=False)
    online_sellable = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code', 'id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'code'], name='location_code_unique_per_company'),
            models.UniqueConstraint(
                fields=['company', 'type'], condition=models.Q(type__in=VIRTUAL_LOCATION_TYPES),
                name='one_virtual_location_per_type_per_company',
            ),
        ]

    def __str__(self):
        return f'{self.code} · {self.name}'

    @property
    def is_virtual(self):
        return self.type in self.VIRTUAL_TYPES

    def clean(self):
        errors = {}
        if self.online_sellable and not self.sellable:
            errors['online_sellable'] = 'Only a sellable location can count towards online stock.'
        if self.is_virtual:
            if self.sellable or self.online_sellable:
                errors['sellable'] = 'A virtual location is never sellable.'
            if self.branch_id:
                errors['branch'] = 'A virtual location has no branch.'
        elif self.type == self.DISPATCH and (self.sellable or self.online_sellable):
            errors['sellable'] = 'Picked orders wait at dispatch; it is never sellable.'
        elif self.type in self.BRANCH_TYPES and not self.branch_id:
            errors['branch'] = 'This type of location is inside a branch.'
        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            errors['branch'] = 'The branch belongs to another company.'
        if errors:
            raise ValidationError(errors)


class Transfer(models.Model):
    """
    Stock moving between two of the company's own locations. A draft moves nothing; dispatching takes the lines'
    units (earliest expiry first) to the company's IN-TRANSIT location; receiving books what arrived at the
    destination and sends any shortfall to ADJUSTMENT with a reason. Only a draft can be cancelled.
    """

    DRAFT, DISPATCHED, RECEIVED, CANCELLED = 'draft', 'dispatched', 'received', 'cancelled'
    STATUSES = [(DRAFT, 'Draft'), (DISPATCHED, 'Dispatched'), (RECEIVED, 'Received'), (CANCELLED, 'Cancelled')]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='transfers')
    number = models.PositiveIntegerField()  # per company: 1, 2, 3...
    source_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='transfers_out')
    destination_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='transfers_in')
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    received_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-number', '-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'number'], name='transfer_number_unique_per_company'),
        ]

    def __str__(self):
        return self.reference

    @property
    def reference(self):
        return f'T-{self.number:06d}' if self.number else 'T-new'


class TransferLine(models.Model):
    """A product and how many units to send; after receipt, how many arrived and why any differ."""

    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='+')
    quantity = models.PositiveIntegerField()
    received_quantity = models.PositiveIntegerField(null=True, blank=True)
    difference_reason = models.CharField(max_length=255, blank=True)
    # Set when the line fulfils (part of) a store requisition line.
    requisition_line = models.ForeignKey(
        'inventory.RequisitionLine', null=True, blank=True, on_delete=models.PROTECT, related_name='transfer_lines'
    )

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(fields=['transfer', 'product'], name='one_transfer_line_per_product'),
        ]

    def __str__(self):
        return f'{self.transfer} · {self.product} × {self.quantity}'


class TransferPick(models.Model):
    """
    Units dispatch put in an IN-TRANSIT batch for a line. Two transfers of the same lot share that batch row, so
    receiving uses the picks, not the row's whole quantity.
    """

    line = models.ForeignKey(TransferLine, on_delete=models.CASCADE, related_name='picks')
    transit_batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name='+')
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ['pk']


class Requisition(models.Model):
    """
    A store asking the warehouse for stock. The warehouse fulfils it with transfers (``fulfil``), in one go or in
    parts; what is not yet on a transfer stays open. Cancelling closes what is still open (transfers already made
    stay). The status follows the lines: open → partly fulfilled → fulfilled, or cancelled.
    """

    OPEN, PARTLY_FULFILLED, FULFILLED, CANCELLED = 'open', 'partly_fulfilled', 'fulfilled', 'cancelled'
    STATUSES = [(OPEN, 'Open'), (PARTLY_FULFILLED, 'Partly fulfilled'), (FULFILLED, 'Fulfilled'),
                (CANCELLED, 'Cancelled')]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='requisitions')
    number = models.PositiveIntegerField()  # per company: 1, 2, 3...
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='requisitions',
                                 help_text='The store location asking for the stock.')
    status = models.CharField(max_length=16, choices=STATUSES, default=OPEN)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-number', '-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'number'], name='requisition_number_unique_per_company'),
        ]

    def __str__(self):
        return self.reference

    @property
    def reference(self):
        return f'R-{self.number:06d}' if self.number else 'R-new'

    def transfers(self):
        """The transfers made for this requisition, oldest first."""
        return Transfer.objects.filter(lines__requisition_line__requisition=self).distinct().order_by('number')


class RequisitionLine(models.Model):
    """A product and how many units the store asks for."""

    requisition = models.ForeignKey(Requisition, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='+')
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(fields=['requisition', 'product'], name='one_requisition_line_per_product'),
        ]

    def __str__(self):
        return f'{self.requisition} · {self.product} × {self.quantity}'

    @property
    def fulfilled_quantity(self):
        """Units on transfers for this line, except cancelled ones (a short receipt still counts as sent)."""
        return self.transfer_lines.exclude(transfer__status=Transfer.CANCELLED).aggregate(
            total=models.Sum('quantity'))['total'] or 0

    @property
    def open_quantity(self):
        return max(self.quantity - self.fulfilled_quantity, 0)


class BarcodeRule(models.Model):
    """
    How the company's scales lay out a variable-measure EAN-13 label (G15): ``prefix | item code | [value check
    digit] | value | check digit``. The value (a price in GBP or a weight in kg, with ``decimals``) takes the digits
    left over. Read with ``inventory.barcode_parser.parse(code, [rule.to_rule() ...])``; the longest prefix wins.
    """

    PRICE, WEIGHT = 'price', 'weight'
    VALUE_TYPES = [(PRICE, 'Price (GBP)'), (WEIGHT, 'Weight (kg)')]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='barcode_rules')
    name = models.CharField(max_length=100, blank=True)
    prefix = models.CharField(max_length=3, help_text='1 to 3 digits starting with 2, e.g. 20.')
    item_code_length = models.PositiveSmallIntegerField()
    value_type = models.CharField(max_length=10, choices=VALUE_TYPES)
    decimals = models.PositiveSmallIntegerField(default=2)
    value_check_digit = models.BooleanField(default=False, help_text='A GS1 check digit sits before the value.')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['prefix']
        constraints = [
            models.UniqueConstraint(fields=['company', 'prefix'], name='unique_barcode_prefix_per_company'),
        ]

    def __str__(self):
        return f'{self.prefix}… {self.name or self.get_value_type_display()}'

    def to_rule(self):
        from .barcode_parser import Rule

        return Rule(prefix=self.prefix, item_code_length=self.item_code_length, value_type=self.value_type,
                    decimals=self.decimals, value_check_digit=self.value_check_digit, is_active=self.is_active)

    def clean(self):
        layout = (self.prefix, self.item_code_length, self.value_type, self.decimals, self.value_check_digit)
        if any(value in (None, '') for value in layout):
            return  # the field errors already say what is missing
        error = self.to_rule().layout_error()
        if error:
            field, message = error.split(': ', 1)
            raise ValidationError({field: message})
        # The admin leaves company out of the form, so the unique constraint is not checked there.
        if self.company_id and BarcodeRule.objects.filter(company_id=self.company_id, prefix=self.prefix).exclude(
                pk=self.pk).exists():
            raise ValidationError({'prefix': 'Another rule already uses this prefix.'})


class YieldTemplate(models.Model):
    """
    How the butchery expects an input (e.g. a side of beef, sold by weight) to break down (G20): product outputs and
    waste (bone, fat, trim), each an expected percentage of the input's weight. They total at most 100; the rest is
    the expected loss (moisture, saw dust). Breakdown orders (G21) start from a template.
    """

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='yield_templates')
    name = models.CharField(max_length=100)
    input_product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='yield_templates')
    # How a breakdown spreads the input's cost over the product outputs (G22): by their kg, or by their value
    # (kg × price per kg). Waste carries none.
    BY_WEIGHT, BY_VALUE = 'weight', 'value'
    COST_METHODS = [(BY_WEIGHT, 'By weight'), (BY_VALUE, 'By value')]
    cost_method = models.CharField(max_length=10, choices=COST_METHODS, default=BY_WEIGHT)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['company', 'name'], name='unique_yield_template_name_per_company'),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        # The API scopes products to the user's company; this guards the admin for superusers (not scoped).
        if self.input_product_id:
            problem = other_company_problem(self.input_product, self.company_id)
            if problem:
                raise ValidationError({'input_product': problem})

    @staticmethod
    def percent_totals(outputs):
        """``(yield, waste, loss)`` percentages for ``outputs`` (objects or dicts with ``kind``/``expected_percent``)."""
        def get(output, name):
            return output[name] if isinstance(output, dict) else getattr(output, name)
        yielded = sum((get(o, 'expected_percent') for o in outputs if get(o, 'kind') == YieldTemplateOutput.PRODUCT),
                      Decimal('0.00'))
        waste = sum((get(o, 'expected_percent') for o in outputs if get(o, 'kind') == YieldTemplateOutput.WASTE),
                    Decimal('0.00'))
        return yielded, waste, Decimal('100.00') - yielded - waste

    @property
    def expected_loss_percent(self):
        return self.percent_totals(list(self.outputs.all()))[2]


class YieldTemplateOutput(models.Model):
    """One output of a yield template: a weighed product, or named waste (no product, goes to the waste location)."""

    PRODUCT, WASTE = 'product', 'waste'
    KINDS = [(PRODUCT, 'Product'), (WASTE, 'Waste')]

    template = models.ForeignKey(YieldTemplate, on_delete=models.CASCADE, related_name='outputs')
    kind = models.CharField(max_length=10, choices=KINDS, default=PRODUCT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True,
                                related_name='yield_outputs')
    name = models.CharField(max_length=100, blank=True, help_text='The waste, e.g. Bone; products use their name.')
    expected_percent = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('100'))])
    position = models.PositiveSmallIntegerField(default=0)
    # A product output's batches expire this many days after the breakdown, never after the input (G21); blank:
    # the input's expiry.
    keeps_days = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['template', 'position', 'pk']

    def __str__(self):
        return f'{self.label} {self.expected_percent}%'

    @property
    def label(self):
        return self.product.name if self.product_id else self.name


def weighed_product_problem(product):
    """Why ``product`` cannot be in a breakdown (it must be sold by weight), or None."""
    if product is not None and not product.is_weighed:
        return f'{product.name} is sold by each; a breakdown needs a weighed product.'
    return None


def other_company_problem(product, company_id):
    """Why ``product`` cannot be used by ``company_id`` (it is another company's), or None."""
    if product is not None and company_id is not None and product.company_id != company_id:
        return f'{product.name} belongs to another company.'
    return None


def yield_output_problems(input_product, outputs):
    """
    What is wrong with a yield template's ``outputs`` (dicts: ``kind``, ``product``, ``name``, ``expected_percent``),
    as messages for the person setting it up; empty when it is fine. Shared by the API and the admin.
    """
    problems, seen = [], {}  # key -> the label first given
    for output in outputs:
        product, name = output.get('product'), (output.get('name') or '').strip()
        if output.get('kind') == YieldTemplateOutput.WASTE:
            if product is not None:
                problems.append('Waste is not a product; leave the product out.')
            elif not name:
                problems.append('Name the waste (e.g. Bone).')
                continue
            if output.get('keeps_days') is not None:
                problems.append('Waste is thrown away; leave keeps days out.')
            key = ('waste', name.casefold())
        elif product is None:
            problems.append('Choose the output product.')
            continue
        else:
            if input_product is not None and product.pk == input_product.pk:
                problems.append(f'{product.name} is the input; it cannot be an output too.')
            problem = weighed_product_problem(product)
            if problem:
                problems.append(problem)
            key = ('product', product.pk)
        if key in seen:
            problems.append(f'{seen[key]} is listed twice.')
        seen.setdefault(key, product.name if product is not None else name)
    if not any(o.get('kind') == YieldTemplateOutput.PRODUCT for o in outputs):
        problems.append('Add at least one product the input breaks down into.')
    total = sum((o.get('expected_percent') or Decimal('0') for o in outputs), Decimal('0.00'))
    if total > 100:
        problems.append(f'Outputs add up to {total}%; they can be at most 100%.')
    return problems


class Breakdown(models.Model):
    """
    A butchery breakdown (G21): part or all of an input batch at a butchery location cut into the template's
    products (new batches at the same location), waste (to WASTE) and loss (to ADJUSTMENT), so ``input_kg`` = product
    kg + waste kg + ``loss_kg`` exactly. Recorded in one step (``inventory.breakdowns``); a void reverses it.
    """

    COMPLETED, VOIDED = 'completed', 'voided'
    STATUSES = [(COMPLETED, 'Completed'), (VOIDED, 'Voided')]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='breakdowns')
    number = models.PositiveIntegerField()  # per company: 1, 2, 3...
    template = models.ForeignKey(YieldTemplate, on_delete=models.PROTECT, related_name='breakdowns')
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='breakdowns')
    input_batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name='breakdowns')
    input_pieces = models.PositiveIntegerField(default=0)  # catch-weight inputs
    input_kg = models.DecimalField(max_digits=10, decimal_places=3)
    loss_kg = models.DecimalField(max_digits=10, decimal_places=3, default=Decimal('0.000'))
    loss_reason = models.CharField(max_length=255, blank=True)
    input_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # null: batch had none
    cost_method = models.CharField(max_length=10, choices=YieldTemplate.COST_METHODS, blank=True)  # as used
    status = models.CharField(max_length=10, choices=STATUSES, default=COMPLETED)
    client_ref = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-number', '-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'number'], name='breakdown_number_unique_per_company'),
            models.UniqueConstraint(fields=['company', 'client_ref'], condition=models.Q(client_ref__isnull=False),
                                    name='breakdown_client_ref_unique_per_company'),
        ]

    def __str__(self):
        return self.reference

    @property
    def reference(self):
        return f'BD-{self.number:06d}' if self.number else 'BD-new'


class BreakdownLine(models.Model):
    """What came out of a breakdown for one template output: a product's new batch, or waste."""

    breakdown = models.ForeignKey(Breakdown, on_delete=models.CASCADE, related_name='lines')
    template_output = models.ForeignKey(YieldTemplateOutput, on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=10, choices=YieldTemplateOutput.KINDS)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    name = models.CharField(max_length=100)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3)
    pieces = models.PositiveIntegerField(default=0)  # catch-weight outputs
    expiration_date = models.DateField(null=True, blank=True)  # products
    output_batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    allocated_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # products, G22

    class Meta:
        ordering = ['breakdown', 'pk']

    def __str__(self):
        return f'{self.name} {self.weight_kg} kg'


class WriteOff(models.Model):
    """
    Stock written off to the company's WASTE location (G23), with a reason. Its cost is the amount × the batch's
    landed ``unit_cost``. Up to the company's threshold (``Setting`` ``write_off_approval_threshold``) it moves at
    once; above it, or with no known cost, it waits ``pending`` until a staff member other than the one who raised it
    approves (stock moves then) or rejects it. See ``inventory.write_offs``.
    """

    PENDING, WRITTEN_OFF, REJECTED = 'pending', 'written_off', 'rejected'
    STATUSES = [(PENDING, 'Waiting for approval'), (WRITTEN_OFF, 'Written off'), (REJECTED, 'Rejected')]
    REASONS = [('expired', 'Expired'), ('damaged', 'Damaged'), ('trim', 'Trim'), ('quality', 'Quality'),
               ('theft', 'Theft'), ('sample', 'Sample')]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='write_offs')
    number = models.PositiveIntegerField()  # per company: 1, 2, 3...
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name='write_offs')
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='write_offs')
    reason = models.CharField(max_length=10, choices=REASONS)
    quantity = models.PositiveIntegerField(default=0)  # units, or pieces for catch-weight; 0 for loose products
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)  # weighed products
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # null: batch had no cost
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=PENDING)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)
    written_off_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-number', '-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'number'], name='write_off_number_unique_per_company'),
        ]

    def __str__(self):
        return self.reference

    @property
    def reference(self):
        return f'W-{self.number:06d}' if self.number else 'W-new'
