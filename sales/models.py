# sales/models.py

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from vat.models import VAT_CODES


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
    # The standalone card terminal (G36): card sales at close, and the terminal's own end-of-day total (null when it
    # was not entered). Their difference is the card variance, kept next to the cash one.
    expected_card = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    counted_card = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
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
    # A sale made while the till was offline (G27/G28): its offline receipt number and when the till made it.
    offline_number = models.CharField(max_length=32, blank=True, db_index=True)
    sold_at = models.DateTimeField(null=True, blank=True)
    # The standalone card terminal's auth code, from its slip (G27 offline, G36 online), and optionally the card's last
    # 4 digits. Never more of the card number than that.
    card_auth_code = models.CharField(max_length=12, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'client_ref'], condition=Q(client_ref__isnull=False),
                name='one_sale_per_client_ref',
            ),
            models.UniqueConstraint(
                fields=['session', 'offline_number'], condition=~Q(offline_number=''),
                name='one_sale_per_offline_number',
            ),
        ]

    def __str__(self):
        return self.receipt_number

    @property
    def receipt_number(self):
        return f'S-{self.pk:06d}' if self.pk else 'S-new'


class SaleItem(models.Model):
    """
    One product line on a sale. Name, price and VAT are copied so the receipt never changes afterwards.

    ``line_total`` is the gross (till prices include VAT); ``net_amount + vat_amount == line_total``. The VAT fields
    are empty on lines sold before VAT was recorded.
    """

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='+')
    product_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    # Weighed lines: the kg sold and the price per kg used (``unit_price`` is that price rounded to the penny).
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    price_per_kg = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    # Promotions (G58): what came off this line, gross; ``line_total`` is after it and VAT is split from that.
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    vat_code = models.CharField(max_length=10, choices=VAT_CODES, blank=True)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # A scanned label (G18): the raw code as sent, the price printed on it (charged as it is), what the product
    # costs today when that can be compared, and whether the two are further apart than the company's tolerance.
    barcode = models.CharField(max_length=64, blank=True)
    label_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    expected_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    label_price_flagged = models.BooleanField(default=False)
    # The cashier typed the weight (no label, no scale reading): kept for audit (G19).
    manual_weight = models.BooleanField(default=False)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.quantity} x {self.product_name}'


class SalePromotion(models.Model):
    """A promotion a sale got (G58), as charged: its name and saving are copied, so the receipt never changes."""

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='promotions')
    promotion = models.ForeignKey('promotions.Promotion', null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='+')
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=20)
    times = models.PositiveIntegerField()
    saving = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.name}: -{self.saving}'


class SaleAllocation(models.Model):
    """Which stock batch the units of a sale line came from (oldest expiry first), so a void can put them back."""

    item = models.ForeignKey(SaleItem, on_delete=models.CASCADE, related_name='allocations')
    batch = models.ForeignKey('inventory.StockBatch', on_delete=models.PROTECT, related_name='+')
    quantity = models.PositiveIntegerField()
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)

    def __str__(self):
        return f'{self.quantity} from {self.batch}'


class SaleException(models.Model):
    """
    Something about an offline sale that didn't match the server when it was synced (G28, ADR 0001). The sale stands
    (it was paid); a manager reviews these (G29).
    """

    PRICE, STOCK, LABEL, WEIGHT_LIMIT, SESSION_CLOSED, CASHIER, PRODUCT_CHANGED, TENDER = (
        'price', 'stock', 'label', 'weight_limit', 'session_closed', 'cashier', 'product_changed', 'tender')
    KINDS = [(PRICE, 'Price differs'), (STOCK, 'Not enough stock'), (LABEL, 'Label not readable as sold'),
             (WEIGHT_LIMIT, 'Typed weight over the limit'), (SESSION_CLOSED, 'Session already closed'),
             (CASHIER, 'Cashier not found'), (PRODUCT_CHANGED, 'Product changed since the sale'),
             (TENDER, 'Cash tendered below the total')]

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='exceptions')
    item = models.ForeignKey(SaleItem, null=True, blank=True, on_delete=models.CASCADE, related_name='exceptions')
    kind = models.CharField(max_length=20, choices=KINDS)
    product = models.ForeignKey('inventory.Product', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    expected = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # the server's price
    charged = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # what the till charged
    quantity = models.IntegerField(null=True, blank=True)  # units or pieces short
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)  # kg short
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.sale.receipt_number}: {self.kind}'
