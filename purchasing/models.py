"""
Purchasing (G57): which supplier sells us each product and in what packs, and purchase orders drafted from stock
projections. Orders are drafted and approved here; nothing is sent to a supplier.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class SupplierProduct(models.Model):
    """A product as a supplier sells it: in packs of ``pack_size`` units at ``pack_cost`` (ex VAT)."""

    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.CASCADE, related_name='products')
    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE, related_name='supplier_links')
    pack_size = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)],
                                            help_text='Units per pack (case).')
    pack_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                    validators=[MinValueValidator(Decimal('0'))], help_text='Per pack, ex VAT.')
    preferred = models.BooleanField(default=False, help_text='Orders are drafted to the preferred supplier only.')

    class Meta:
        ordering = ['product', 'supplier']
        constraints = [
            models.UniqueConstraint(fields=['supplier', 'product'], name='unique_supplier_product'),
            models.UniqueConstraint(fields=['product'], condition=Q(preferred=True),
                                    name='one_preferred_supplier_per_product'),
            models.CheckConstraint(condition=Q(pack_size__gte=1), name='supplier_product_pack_size'),
        ]

    def __str__(self):
        return f'{self.product} from {self.supplier}'


class PurchaseOrder(models.Model):
    DRAFT, APPROVED, CANCELLED = 'draft', 'approved', 'cancelled'
    STATUSES = [(DRAFT, 'Draft'), (APPROVED, 'Approved'), (CANCELLED, 'Cancelled')]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='purchase_orders')
    number = models.PositiveIntegerField()
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.PROTECT, related_name='purchase_orders')
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT)
    auto = models.BooleanField(default=True, help_text='Drafted by the system (a run may replace it untouched).')
    edited = models.BooleanField(default=False, help_text='Changed by staff: runs leave it alone.')
    below_minimum = models.BooleanField(default=False, help_text="Under the supplier's minimum order.")
    expected_on = models.DateField(help_text='When it would arrive if ordered now (or when approved).')
    total_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'),
                                     help_text='Ex VAT; lines without a pack cost count 0.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='unique_purchase_order_number')]

    def __str__(self):
        return self.number_display

    @property
    def number_display(self):
        return f'PO-{self.number:06d}'


class PurchaseOrderLine(models.Model):
    """What to order of one product, and why (the numbers it was worked out from)."""

    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='+')
    packs = models.PositiveIntegerField()
    pack_size = models.PositiveIntegerField()
    pack_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    line_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    daily_demand = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    horizon_days = models.PositiveSmallIntegerField(default=0)
    target = models.PositiveIntegerField(default=0, help_text='Expected demand over the horizon + safety stock.')
    on_hand = models.IntegerField(default=0)
    on_order = models.PositiveIntegerField(default=0)
    events = models.CharField(max_length=255, blank=True, help_text='Demand events counted.')
    stockout_date = models.DateField(null=True, blank=True)
    topped_up = models.PositiveIntegerField(default=0, help_text='Packs added to reach the minimum order.')

    class Meta:
        ordering = ['id']
        constraints = [models.UniqueConstraint(fields=['order', 'product'], name='unique_purchase_order_product')]

    @property
    def units(self):
        return self.packs * self.pack_size

    def __str__(self):
        return f'{self.packs} × {self.pack_size} {self.product}'
