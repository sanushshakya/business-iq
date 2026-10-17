# sales/models.py

from django.conf import settings
from django.db import models
from django.db.models import Q


class TillSession(models.Model):
    """
    A shift on a till: opened with a cash float, closed with a cash count.

    A till has at most one open session. Sales are rung up against the open session, and closing it
    compares the counted cash with what the drawer should hold (float plus cash sales).
    """

    till = models.ForeignKey('tenants.Till', on_delete=models.PROTECT, related_name='sessions')
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_float = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    # Set when the session is closed, so the figures stay as they were at cash-up.
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    counted_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-opened_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['till'], condition=Q(closed_at__isnull=True), name='one_open_session_per_till'
            ),
        ]

    def __str__(self):
        return f'{self.till} session {self.pk}'

    @property
    def is_open(self):
        return self.closed_at is None


class Sale(models.Model):
    """A completed (or voided) transaction rung up at a till."""

    CASH, CARD = 'cash', 'card'
    PAYMENT_METHODS = [(CASH, 'Cash'), (CARD, 'Card')]
    COMPLETED, VOIDED = 'completed', 'voided'
    STATUSES = [(COMPLETED, 'Completed'), (VOIDED, 'Voided')]

    session = models.ForeignKey(TillSession, on_delete=models.PROTECT, related_name='sales')
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHODS)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    amount_tendered = models.DecimalField(max_digits=12, decimal_places=2)
    change_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUSES, default=COMPLETED)
    created_at = models.DateTimeField(auto_now_add=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    void_reason = models.CharField(max_length=255, blank=True)
    # Chosen by the till for each sale, so resending after a lost response returns the original sale
    # instead of charging twice.
    client_ref = models.UUIDField(null=True, blank=True)
    # The customer's storefront loyalty card, if scanned; the points are awarded (and reversed on a void) in m18.
    loyalty_barcode = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'client_ref'], condition=Q(client_ref__isnull=False),
                name='one_sale_per_client_ref',
            ),
        ]

    def __str__(self):
        return self.receipt_number

    @property
    def receipt_number(self):
        return f'S-{self.pk:06d}' if self.pk else 'S-new'


class SaleItem(models.Model):
    """One product line on a sale. Name and price are copied so the receipt never changes afterwards."""

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='+')
    product_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.quantity} x {self.product_name}'


class SaleAllocation(models.Model):
    """Which stock batch the units of a sale line came from (oldest expiry first), so a void can put them back."""

    item = models.ForeignKey(SaleItem, on_delete=models.CASCADE, related_name='allocations')
    batch = models.ForeignKey('inventory.StockBatch', on_delete=models.PROTECT, related_name='+')
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f'{self.quantity} from {self.batch}'
