"""
inventory/models.py

This module contains Django models for the inventory app. These models are responsible
for representing and managing inventory-related data within the application.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'external_id'], condition=~models.Q(external_id=''),
                name='unique_external_id_per_company',
            ),
        ]

    def __str__(self):
        return self.name

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
    receive_date = models.DateTimeField(auto_now_add=True)
    expiration_date = models.DateField()
    # Where this lot is. A lot split across places is one row per place (same number and expiry).
    location = models.ForeignKey('inventory.Location', on_delete=models.PROTECT, related_name='batches')

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

    def __str__(self):
        return f"{self.batch.product.name} - {self.movement_type.capitalize()} - {self.quantity}"

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
    IN_TRANSIT, SUPPLIER, CUSTOMER, WASTE, ADJUSTMENT = 'in_transit', 'supplier', 'customer', 'waste', 'adjustment'
    TYPES = [
        (SHOP_FLOOR, 'Shop floor'),
        (BUTCHERY, 'Butchery'),
        (WAREHOUSE, 'Warehouse'),
        (BACK_STORE, 'Back store'),
        (VAN, 'Van'),
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
