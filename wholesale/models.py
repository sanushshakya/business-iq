"""
B2B wholesale (M7). G38: trade customers — the restaurants and shops that buy on account, with their delivery
addresses, contacts and the days they take deliveries. G39: customer groups and price lists (ex VAT, with quantity
breaks and dates; resolved in ``pricing.py``). Orders (G40+) and credit build on these.

Contacts and addresses are business details but may name people: never log them, and use obviously fake data in
tests.
"""

import re
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from vat.models import VAT_CODES
from vat.numbers import VAT_NUMBER, normalise_vat_number, vat_number_problem  # noqa: F401 (used here and re-exported)

WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
ACCOUNT_CODE = re.compile(r'[A-Z0-9][A-Z0-9-]{0,19}', re.ASCII)
POSTCODE = re.compile(r'[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}', re.ASCII)


def normalise_account_code(value):
    return (value or '').strip().upper()


def account_code_problem(code):
    if not ACCOUNT_CODE.fullmatch(code):
        return 'Use 1-20 capital letters, digits or dashes, starting with a letter or digit.'
    return None


def normalise_postcode(value):
    compact = re.sub(r'\s', '', value or '').upper()
    return f'{compact[:-3]} {compact[-3:]}' if len(compact) > 3 else compact


def postcode_problem(postcode):
    return None if POSTCODE.fullmatch(postcode) else 'Not a UK postcode.'


def delivery_days_problem(days):
    if not isinstance(days, list) or any(day not in WEEKDAYS for day in days):
        return f'A list of days from {", ".join(WEEKDAYS)}.'
    if len(set(days)) != len(days):
        return 'Each day once.'
    return None


def in_week_order(days):
    return sorted(days, key=WEEKDAYS.index)


class CustomerGroup(models.Model):
    """Trade customers priced alike (e.g. "Restaurants"); a customer is in at most one."""

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='customer_groups')
    name = models.CharField(max_length=80)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['name', 'id']
        constraints = [models.UniqueConstraint(fields=['company', 'name'], name='unique_customer_group_name')]

    def __str__(self):
        return self.name


class TradeCustomer(models.Model):
    """A business that buys on account. Closed customers stay (orders and invoices will point at them)."""

    ACTIVE, ON_HOLD, CLOSED = 'active', 'on_hold', 'closed'
    STATUSES = [(ACTIVE, 'Active'), (ON_HOLD, 'On hold'), (CLOSED, 'Closed')]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='trade_customers')
    account_code = models.CharField(max_length=20, help_text='Unique per company: capitals, digits and dashes.')
    legal_name = models.CharField(max_length=150)
    trading_name = models.CharField(max_length=150, blank=True)
    vat_number = models.CharField(max_length=14, blank=True, help_text='UK VAT number, e.g. GB123456789.')
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE)
    delivery_days = models.JSONField(default=list, blank=True, help_text='Days it takes deliveries, e.g. ["mon"].')
    note = models.TextField(blank=True)
    # Its group's price lists apply after its own (G39); deleting the group leaves it with none.
    group = models.ForeignKey(CustomerGroup, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='customers')
    # Invoicing (G43): where invoices are emailed (blank: the primary contact's email) and how long to pay.
    invoice_email = models.EmailField(blank=True, help_text="Where invoices go; blank: the primary contact's email.")
    payment_terms_days = models.PositiveSmallIntegerField(default=30, validators=[MaxValueValidator(120)],
                                                          help_text='Days to pay an invoice (0-120).')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['account_code', 'id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'account_code'], name='unique_trade_customer_code'),
        ]

    def __str__(self):
        return f'{self.account_code} {self.trading_name or self.legal_name}'

    def clean(self):
        self.account_code = normalise_account_code(self.account_code)
        self.vat_number = normalise_vat_number(self.vat_number)
        problems = {}
        for field, problem in (
            ('account_code', account_code_problem(self.account_code)),
            ('vat_number', vat_number_problem(self.vat_number)),
            ('delivery_days', delivery_days_problem(self.delivery_days)),
        ):
            if problem:
                problems[field] = problem
        if problems:
            raise ValidationError(problems)
        self.delivery_days = in_week_order(self.delivery_days)


class TradeCustomerAddress(models.Model):
    customer = models.ForeignKey(TradeCustomer, on_delete=models.CASCADE, related_name='addresses')
    label = models.CharField(max_length=60, help_text='e.g. "Kitchen door".')
    line1 = models.CharField(max_length=120)
    line2 = models.CharField(max_length=120, blank=True)
    town = models.CharField(max_length=80)
    postcode = models.CharField(max_length=8)
    instructions = models.CharField(max_length=255, blank=True, help_text='For the driver.')
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ['customer', '-is_default', 'id']
        verbose_name_plural = 'trade customer addresses'
        constraints = [
            models.UniqueConstraint(fields=['customer'], condition=Q(is_default=True),
                                    name='one_default_address_per_customer'),
        ]

    def __str__(self):
        return f'{self.label}, {self.postcode}'

    def clean(self):
        self.postcode = normalise_postcode(self.postcode)
        problem = postcode_problem(self.postcode)
        if problem:
            raise ValidationError({'postcode': problem})


class TradeCustomerContact(models.Model):
    customer = models.ForeignKey(TradeCustomer, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=60, blank=True, help_text='e.g. "Head chef", "Accounts".')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ['customer', '-is_primary', 'id']
        constraints = [
            models.UniqueConstraint(fields=['customer'], condition=Q(is_primary=True),
                                    name='one_primary_contact_per_customer'),
        ]

    def __str__(self):
        # Not the person's name: this string is kept in the admin's change history.
        return self.role or f'Contact #{self.pk}'


PRIORITY_LIMIT = 1000
KG = Decimal('0.001')


def dates_problem(valid_from, valid_to):
    if valid_from and valid_to and valid_to < valid_from:
        return 'Ends before it starts.'
    return None


def min_quantity_problem(product, min_quantity):
    """Units for each products (whole), kg to 3 dp for weighed ones; always above 0."""
    if min_quantity is None or min_quantity <= 0:
        return 'Above 0.'
    if product.is_weighed:
        return None if min_quantity == min_quantity.quantize(KG) else 'Kg to 3 decimal places.'
    return None if min_quantity == min_quantity.to_integral_value() else 'A whole number of units.'


class PriceList(models.Model):
    """
    Trade prices ex VAT for the customers and groups it is assigned to (G39). A list with neither assigned is a
    draft and applies to nobody. Valid on UK dates, inclusive at both ends; open-ended when a date is blank. Where
    several lists price a product at one level, the highest ``priority`` wins, then the newest.
    """

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='price_lists')
    name = models.CharField(max_length=100)
    priority = models.IntegerField(default=0, validators=[MinValueValidator(-PRIORITY_LIMIT),
                                                          MaxValueValidator(PRIORITY_LIMIT)],
                                   help_text='Higher wins where lists overlap (-1000 to 1000).')
    is_active = models.BooleanField(default=True)
    valid_from = models.DateField(null=True, blank=True, help_text='First UK day it applies; blank: always.')
    valid_to = models.DateField(null=True, blank=True, help_text='Last UK day it applies; blank: no end.')
    customers = models.ManyToManyField(TradeCustomer, blank=True, related_name='price_lists')
    groups = models.ManyToManyField(CustomerGroup, blank=True, related_name='price_lists')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-priority', '-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'name'], name='unique_price_list_name'),
            models.CheckConstraint(condition=Q(priority__gte=-PRIORITY_LIMIT, priority__lte=PRIORITY_LIMIT),
                                   name='price_list_priority_range'),
            models.CheckConstraint(
                condition=Q(valid_from__isnull=True) | Q(valid_to__isnull=True) | Q(valid_to__gte=F('valid_from')),
                name='price_list_dates_in_order'),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        problem = dates_problem(self.valid_from, self.valid_to)
        if problem:
            raise ValidationError({'valid_to': problem})


class PriceListEntry(models.Model):
    """One price break: from ``min_quantity`` (units, or kg for weighed products) the whole line costs
    ``unit_price`` ex VAT per unit (per kg)."""

    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name='entries')
    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE, related_name='price_list_entries')
    min_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('1'),
                                       validators=[MinValueValidator(KG)],
                                       help_text='From this many units (kg when sold by weight).')
    unit_price = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(Decimal('0'))],
                                     help_text='Ex VAT, per unit (per kg when sold by weight).')

    class Meta:
        ordering = ['price_list', 'product', 'min_quantity']
        verbose_name_plural = 'price list entries'
        constraints = [
            models.UniqueConstraint(fields=['price_list', 'product', 'min_quantity'], name='unique_price_break'),
            models.CheckConstraint(condition=Q(min_quantity__gt=0), name='price_break_quantity_positive'),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name='price_break_price_not_negative'),
        ]

    def __str__(self):
        return f'{self.product} from {self.min_quantity}'

    def clean(self):
        if self.product_id and self.min_quantity is not None:
            problem = min_quantity_problem(self.product, self.min_quantity)
            if problem:
                raise ValidationError({'min_quantity': problem})


class SalesOrder(models.Model):
    """
    A trade customer's order (G40), entered by staff. Lines are priced when the order is confirmed (from the price
    lists valid on the delivery date) and never re-priced; the delivery address is copied, since the customer's
    addresses are recreated when edited. Status moves only through ``wholesale.orders``.
    """

    DRAFT, CONFIRMED, PICKING, PICKED = 'draft', 'confirmed', 'picking', 'picked'
    DISPATCHED, DELIVERED, INVOICED, CANCELLED = 'dispatched', 'delivered', 'invoiced', 'cancelled'
    STATUSES = [(DRAFT, 'Draft'), (CONFIRMED, 'Confirmed'), (PICKING, 'Picking'), (PICKED, 'Picked'),
                (DISPATCHED, 'Dispatched'), (DELIVERED, 'Delivered'), (INVOICED, 'Invoiced'),
                (CANCELLED, 'Cancelled')]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='sales_orders')
    number = models.PositiveIntegerField()  # per company: 1, 2, 3...
    customer = models.ForeignKey(TradeCustomer, on_delete=models.PROTECT, related_name='orders')
    delivery_date = models.DateField()
    customer_ref = models.CharField(max_length=40, blank=True, help_text="The customer's own order number.")
    note = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=DRAFT)
    # The warehouse it is reserved and picked from (G41): needed to confirm.
    source_location = models.ForeignKey('inventory.Location', on_delete=models.PROTECT, null=True, blank=True,
                                        related_name='sales_orders')
    # The delivery address as it was chosen (a copy).
    delivery_label = models.CharField(max_length=60, blank=True)
    line1 = models.CharField(max_length=120)
    line2 = models.CharField(max_length=120, blank=True)
    town = models.CharField(max_length=80)
    postcode = models.CharField(max_length=8)
    instructions = models.CharField(max_length=255, blank=True)
    # Set at confirmation, ex VAT / VAT / with VAT; estimates where weighed lines are billed on actual weight (G43).
    estimated_net = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    estimated_vat = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    estimated_gross = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+')
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-number']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='unique_sales_order_number')]

    def __str__(self):
        return self.number_display

    @property
    def number_display(self):
        return f'SO-{self.number:06d}'

    @property
    def off_delivery_day(self):
        """Delivered on a day the customer doesn't take deliveries (allowed, but worth a look)."""
        days = self.customer.delivery_days
        return bool(days) and WEEKDAYS[self.delivery_date.weekday()] not in days


class SalesOrderLine(models.Model):
    """
    A product on an order: ``quantity`` units (each) or pieces (catch-weight), or ``weight_kg`` (loose). For
    catch-weight, ``weight_kg`` is the estimate (pieces x nominal) filled at confirmation. The price fields are a
    snapshot taken at confirmation, ex VAT; the net and VAT are rounded half up to the penny.
    """

    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='sales_order_lines')
    quantity = models.PositiveIntegerField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    price_source = models.CharField(max_length=20, blank=True)
    price_list = models.ForeignKey(PriceList, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    price_list_name = models.CharField(max_length=100, blank=True)
    min_quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    vat_code = models.CharField(max_length=10, choices=VAT_CODES, blank=True)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    estimated_net = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    estimated_vat = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # A swap proposed while picking (G42): another product for ``substitute_for``, approved by other staff, then
    # priced like a confirmed line.
    PROPOSED, APPROVED, REJECTED = 'proposed', 'approved', 'rejected'
    SUBSTITUTIONS = [(PROPOSED, 'Proposed'), (APPROVED, 'Approved'), (REJECTED, 'Rejected')]
    substitute_for = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True,
                                       related_name='substitutes')
    substitution = models.CharField(max_length=10, choices=SUBSTITUTIONS, blank=True)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    # What was picked (G42), set when the pick is finished: what the invoice bills (G43).
    picked_quantity = models.PositiveIntegerField(null=True, blank=True)
    picked_weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)

    class Meta:
        ordering = ['order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['order', 'product'], condition=Q(substitute_for__isnull=True),
                                    name='one_line_per_product_per_order'),
        ]

    def __str__(self):
        return f'{self.product} x {self.weight_kg if self.quantity is None else self.quantity}'


class SalesOrderPick(models.Model):
    """
    What a picker took for a line from one warehouse lot (G42): units (pieces for catch-weight; 0 for loose) and the
    real kg for weighed products. Removable until the pick is finished, when it is moved to DISPATCH.
    """

    line = models.ForeignKey(SalesOrderLine, on_delete=models.CASCADE, related_name='picks')
    batch = models.ForeignKey('inventory.StockBatch', on_delete=models.PROTECT, related_name='sales_order_picks')
    quantity = models.PositiveIntegerField(default=0)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    barcode = models.CharField(max_length=64, blank=True, help_text='The label scanned, if any.')
    picked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='+')
    picked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['line', 'id']

    def __str__(self):
        return f'Pick #{self.pk}'


FROZEN = 'An issued invoice cannot be changed; correct it with a credit note.'


class FrozenDocumentQuerySet(models.QuerySet):
    """Bulk updates follow the model: an issued invoice or credit note changes only in its email fields."""

    def update(self, **kwargs):
        if not set(kwargs) <= self.model.EMAIL_FIELDS and self.filter(status='issued').exists():
            raise ValidationError(FROZEN)
        return super().update(**kwargs)


class FrozenLineQuerySet(models.QuerySet):
    """No bulk path writes lines of an issued document (the line model names its ``DOCUMENT_FIELD``)."""

    def _refuse(self, document_ids):
        document = self.model._meta.get_field(self.model.DOCUMENT_FIELD).related_model
        if document.objects.filter(pk__in=set(document_ids), status='issued').exists():
            raise ValidationError(FROZEN)

    def update(self, **kwargs):
        self._refuse(self.values_list(f'{self.model.DOCUMENT_FIELD}_id', flat=True))
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, *args, **kwargs):
        objs = list(objs)
        self._refuse(self.model.objects.filter(pk__in=[obj.pk for obj in objs]).values_list(
            f'{self.model.DOCUMENT_FIELD}_id', flat=True))
        return super().bulk_update(objs, fields, *args, **kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        self._refuse(getattr(obj, f'{self.model.DOCUMENT_FIELD}_id') for obj in objs)
        return super().bulk_create(objs, *args, **kwargs)


InvoiceQuerySet, InvoiceLineQuerySet = FrozenDocumentQuerySet, FrozenLineQuerySet


class Invoice(models.Model):
    """
    A trade invoice for a picked order (G43), billed on what was picked (real kg for weighed goods). A draft has no
    number and can be rebuilt or discarded; issuing numbers it (gap-free per company), copies in who sells to whom
    with both VAT numbers, and freezes it: after that only the email fields change (``save``/delete refuse).
    """

    DRAFT, ISSUED = 'draft', 'issued'
    STATUSES = [(DRAFT, 'Draft'), (ISSUED, 'Issued')]
    NOT_SENT, QUEUED, SENT, FAILED, NO_ADDRESS = 'not_sent', 'queued', 'sent', 'failed', 'no_address'
    EMAIL_STATUSES = [(NOT_SENT, 'Not sent'), (QUEUED, 'Queued'), (SENT, 'Sent'), (FAILED, 'Failed'),
                      (NO_ADDRESS, 'No email address')]
    EMAIL_FIELDS = {'emailed_to', 'emailed_at', 'email_status'}
    objects = InvoiceQuerySet.as_manager()

    company = models.ForeignKey('tenants.Company', on_delete=models.PROTECT, related_name='invoices')
    order = models.OneToOneField(SalesOrder, on_delete=models.PROTECT, related_name='invoice')
    status = models.CharField(max_length=8, choices=STATUSES, default=DRAFT)
    number = models.PositiveIntegerField(null=True, blank=True)  # per company, set at issue: 1, 2, 3...
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    # Copied at issue.
    seller_name = models.CharField(max_length=255, blank=True)
    seller_address = models.TextField(blank=True)
    seller_vat_number = models.CharField(max_length=14, blank=True)
    customer_code = models.CharField(max_length=20, blank=True)
    customer_name = models.CharField(max_length=150, blank=True)
    customer_vat_number = models.CharField(max_length=14, blank=True)
    customer_ref = models.CharField(max_length=40, blank=True)
    address_line1 = models.CharField(max_length=120, blank=True)
    address_line2 = models.CharField(max_length=120, blank=True)
    address_town = models.CharField(max_length=80, blank=True)
    address_postcode = models.CharField(max_length=8, blank=True)
    net_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    vat_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    gross_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    vat_summary = models.JSONField(default=list, blank=True, help_text='By rate: [{rate, net, vat}] as strings.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='+')
    issued_at = models.DateTimeField(null=True, blank=True)
    emailed_to = models.EmailField(blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)
    email_status = models.CharField(max_length=10, choices=EMAIL_STATUSES, default=NOT_SENT)

    class Meta:
        ordering = ['-number', '-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'number'], condition=Q(number__isnull=False),
                                    name='unique_invoice_number'),
            models.CheckConstraint(condition=Q(status='draft', number__isnull=True) | Q(status='issued',
                                                                                       number__isnull=False),
                                   name='invoice_numbered_when_issued'),
        ]

    def __str__(self):
        return self.number_display

    @property
    def number_display(self):
        return f'INV-{self.number:06d}' if self.number else f'Draft #{self.pk}'

    def is_issued_in_db(self):
        return self.pk is not None and Invoice.objects.filter(pk=self.pk, status=self.ISSUED).exists()

    def save(self, *args, update_fields=None, **kwargs):
        if self.is_issued_in_db() and not (update_fields and set(update_fields) <= self.EMAIL_FIELDS):
            raise ValidationError(FROZEN)
        super().save(*args, update_fields=update_fields, **kwargs)

    def delete(self, *args, **kwargs):
        # Re-checked under the row lock, so an issue committed meanwhile is never deleted (the number stays used).
        with transaction.atomic():
            Invoice.objects.select_for_update().filter(pk=self.pk).first()
            _refuse_if_issued(self.pk)
            return super().delete(*args, **kwargs)


class InvoiceLine(models.Model):
    """A billed line: ``quantity`` units or pieces and/or the real ``weight_kg``; net and VAT half up to the
    penny, at the rate fixed on the order line. Frozen once its invoice is issued."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='lines')
    order_line = models.ForeignKey(SalesOrderLine, on_delete=models.PROTECT, related_name='invoice_lines')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='+')
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=4, help_text='Ex VAT, per unit or per kg.')
    vat_code = models.CharField(max_length=10, choices=VAT_CODES, blank=True)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2)
    net = models.DecimalField(max_digits=12, decimal_places=2)
    vat = models.DecimalField(max_digits=12, decimal_places=2)
    gross = models.DecimalField(max_digits=12, decimal_places=2)

    objects = InvoiceLineQuerySet.as_manager()
    DOCUMENT_FIELD = 'invoice'

    class Meta:
        ordering = ['invoice', 'id']

    def __str__(self):
        return self.description

    def save(self, *args, **kwargs):
        if Invoice.objects.filter(pk=self.invoice_id, status=Invoice.ISSUED).exists():
            raise ValidationError(FROZEN)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _refuse_if_issued(self.invoice_id)
        return super().delete(*args, **kwargs)


def _refuse_if_issued(invoice_id):
    if Invoice.objects.filter(pk=invoice_id, status=Invoice.ISSUED).exists():
        raise ValidationError('An issued invoice cannot be deleted; correct it with a credit note.')


@receiver(pre_delete, sender=Invoice)
@receiver(pre_delete, sender=InvoiceLine)
def _issued_invoices_are_kept(sender, instance, **kwargs):
    # Also runs for queryset deletes. An issued invoice is never deleted (numbers stay gap-free).
    _refuse_if_issued(instance.pk if sender is Invoice else instance.invoice_id)


def pod_photo_path(instance, filename):
    """``pod/<company>/<random>.<ext>``: the client's file name is never used."""
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'bin'
    return f'pod/{instance.run.company_id}/{uuid.uuid4().hex}.{extension}'


class DeliveryRun(models.Model):
    """
    A van's delivery run (G44): a date, a van location, a driver (an operator with the driver role) and stops in
    the order they are driven. Starting it moves the orders' picked lots from DISPATCH onto the van; each delivery
    moves an order on to the customer; stops not delivered come back to DISPATCH when it is completed.
    """

    PLANNED, OUT, COMPLETED, CANCELLED = 'planned', 'out', 'completed', 'cancelled'
    STATUSES = [(PLANNED, 'Planned'), (OUT, 'Out'), (COMPLETED, 'Completed'), (CANCELLED, 'Cancelled')]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='delivery_runs')
    number = models.PositiveIntegerField()  # per company: 1, 2, 3...
    run_date = models.DateField()
    vehicle = models.ForeignKey('inventory.Location', on_delete=models.PROTECT, related_name='delivery_runs')
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='delivery_runs')
    status = models.CharField(max_length=10, choices=STATUSES, default=PLANNED)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-run_date', '-number']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='unique_delivery_run_number')]

    def __str__(self):
        return self.number_display

    @property
    def number_display(self):
        return f'RUN-{self.number:06d}'


class DeliveryStop(models.Model):
    """One order on a run, at its place in the drive, with its proof of delivery once delivered."""

    PENDING, DELIVERED, FAILED = 'pending', 'delivered', 'failed'
    STATUSES = [(PENDING, 'Pending'), (DELIVERED, 'Delivered'), (FAILED, 'Not delivered')]

    run = models.ForeignKey(DeliveryRun, on_delete=models.CASCADE, related_name='stops')
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name='delivery_stops')
    position = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    # Proof of delivery. The recipient's name is personal data: shown to staff and the driver, never logged.
    received_by = models.CharField(max_length=120, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    photo = models.FileField(upload_to=pod_photo_path, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+')
    failed_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['run', 'position']
        constraints = [models.UniqueConstraint(fields=['run', 'position'], name='unique_stop_position')]

    def __str__(self):
        return f'Stop {self.position}'


class CustomerReturn(models.Model):
    """
    Goods a trade customer gave back (G45): refused at the door during a delivery (``door``: on the van until the
    run is completed) or returned later (``later``: received when recorded). Each line goes to a location or to
    waste by its reason; a credit note against the issued invoice credits it.
    """

    DOOR, LATER = 'door', 'later'
    KINDS = [(DOOR, 'Refused at the door'), (LATER, 'Returned later')]
    ON_VAN, RECEIVED = 'on_van', 'received'
    STATUSES = [(ON_VAN, 'On the van'), (RECEIVED, 'Received')]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='customer_returns')
    number = models.PositiveIntegerField()  # per company: 1, 2, 3...
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name='returns')
    stop = models.ForeignKey(DeliveryStop, on_delete=models.PROTECT, null=True, blank=True, related_name='returns')
    kind = models.CharField(max_length=6, choices=KINDS)
    status = models.CharField(max_length=10, choices=STATUSES)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-number']
        constraints = [models.UniqueConstraint(fields=['company', 'number'], name='unique_customer_return_number')]

    def __str__(self):
        return self.number_display

    @property
    def number_display(self):
        return f'RET-{self.number:06d}'


class ReturnLine(models.Model):
    """What came back of one order line from one lot (by batch number): units or pieces and/or kg."""

    DAMAGED, OUT_OF_DATE, WRONG_ITEM, NOT_WANTED = 'damaged', 'out_of_date', 'wrong_item', 'not_wanted'
    REASONS = [(DAMAGED, 'Damaged'), (OUT_OF_DATE, 'Out of date'), (WRONG_ITEM, 'Wrong item'),
               (NOT_WANTED, 'Not wanted')]
    WASTE_REASONS = {DAMAGED, OUT_OF_DATE}
    RESTOCK, WASTE = 'restock', 'waste'
    DISPOSITIONS = [(RESTOCK, 'Back to stock'), (WASTE, 'Waste')]

    customer_return = models.ForeignKey(CustomerReturn, on_delete=models.CASCADE, related_name='lines')
    order_line = models.ForeignKey(SalesOrderLine, on_delete=models.PROTECT, related_name='return_lines')
    batch_number = models.CharField(max_length=50)
    quantity = models.PositiveIntegerField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    reason = models.CharField(max_length=12, choices=REASONS)
    disposition = models.CharField(max_length=8, choices=DISPOSITIONS)
    location = models.ForeignKey('inventory.Location', on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='+')

    class Meta:
        ordering = ['customer_return', 'id']

    def __str__(self):
        return f'{self.order_line.product} {self.batch_number}'



class CreditNote(models.Model):
    """
    A credit note (G45) for a return, against the order's issued invoice: its lines credit invoice lines at their
    price and VAT rate, never more than invoiced. Drawn up as a draft (seller and customer copied from the invoice),
    then issued: numbered gap-free per company (``CN-000001``) and frozen like an invoice.
    """

    DRAFT, ISSUED = 'draft', 'issued'
    STATUSES = [(DRAFT, 'Draft'), (ISSUED, 'Issued')]
    EMAIL_STATUSES = Invoice.EMAIL_STATUSES
    EMAIL_FIELDS = Invoice.EMAIL_FIELDS

    company = models.ForeignKey('tenants.Company', on_delete=models.PROTECT, related_name='credit_notes')
    customer_return = models.OneToOneField(CustomerReturn, on_delete=models.PROTECT, related_name='credit_note')
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='credit_notes')
    status = models.CharField(max_length=8, choices=STATUSES, default=DRAFT)
    number = models.PositiveIntegerField(null=True, blank=True)
    credit_date = models.DateField(null=True, blank=True)
    seller_name = models.CharField(max_length=255, blank=True)
    seller_address = models.TextField(blank=True)
    seller_vat_number = models.CharField(max_length=14, blank=True)
    customer_code = models.CharField(max_length=20, blank=True)
    customer_name = models.CharField(max_length=150, blank=True)
    customer_vat_number = models.CharField(max_length=14, blank=True)
    customer_ref = models.CharField(max_length=40, blank=True)
    address_line1 = models.CharField(max_length=120, blank=True)
    address_line2 = models.CharField(max_length=120, blank=True)
    address_town = models.CharField(max_length=80, blank=True)
    address_postcode = models.CharField(max_length=8, blank=True)
    net_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    vat_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    gross_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    vat_summary = models.JSONField(default=list, blank=True, help_text='By rate: [{rate, net, vat}] as strings.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='+')
    issued_at = models.DateTimeField(null=True, blank=True)
    emailed_to = models.EmailField(blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)
    email_status = models.CharField(max_length=10, choices=EMAIL_STATUSES, default=Invoice.NOT_SENT)

    objects = FrozenDocumentQuerySet.as_manager()

    class Meta:
        ordering = ['-number', '-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'number'], condition=Q(number__isnull=False),
                                    name='unique_credit_note_number'),
            models.CheckConstraint(condition=Q(status='draft', number__isnull=True) | Q(status='issued',
                                                                                       number__isnull=False),
                                   name='credit_note_numbered_when_issued'),
        ]

    def __str__(self):
        return self.number_display

    @property
    def number_display(self):
        return f'CN-{self.number:06d}' if self.number else f'Draft #{self.pk}'

    def save(self, *args, update_fields=None, **kwargs):
        issued = self.pk is not None and CreditNote.objects.filter(pk=self.pk, status=self.ISSUED).exists()
        if issued and not (update_fields and set(update_fields) <= self.EMAIL_FIELDS):
            raise ValidationError(FROZEN)
        super().save(*args, update_fields=update_fields, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            CreditNote.objects.select_for_update().filter(pk=self.pk).first()
            _refuse_if_credit_issued(self.pk)
            return super().delete(*args, **kwargs)


class CreditNoteLine(models.Model):
    """Credits one invoice line: units or pieces and/or kg, at the invoice line's price and VAT rate."""

    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name='lines')
    invoice_line = models.ForeignKey(InvoiceLine, on_delete=models.PROTECT, related_name='credit_lines')
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=4)
    vat_code = models.CharField(max_length=10, choices=VAT_CODES, blank=True)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2)
    net = models.DecimalField(max_digits=12, decimal_places=2)
    vat = models.DecimalField(max_digits=12, decimal_places=2)
    gross = models.DecimalField(max_digits=12, decimal_places=2)

    objects = FrozenLineQuerySet.as_manager()
    DOCUMENT_FIELD = 'credit_note'

    class Meta:
        ordering = ['credit_note', 'id']

    def __str__(self):
        return self.description

    def save(self, *args, **kwargs):
        if CreditNote.objects.filter(pk=self.credit_note_id, status=CreditNote.ISSUED).exists():
            raise ValidationError(FROZEN)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _refuse_if_credit_issued(self.credit_note_id)
        return super().delete(*args, **kwargs)


def _refuse_if_credit_issued(credit_note_id):
    if CreditNote.objects.filter(pk=credit_note_id, status=CreditNote.ISSUED).exists():
        raise ValidationError('An issued credit note cannot be deleted.')


@receiver(pre_delete, sender=CreditNote)
@receiver(pre_delete, sender=CreditNoteLine)
def _issued_credit_notes_are_kept(sender, instance, **kwargs):
    _refuse_if_credit_issued(instance.pk if sender is CreditNote else instance.credit_note_id)


LEDGER_FROZEN = 'A ledger entry cannot be changed or deleted; post a reversing adjustment.'


class LedgerEntryQuerySet(models.QuerySet):
    """Only new rows: no bulk path changes or deletes an entry."""

    def update(self, **kwargs):
        raise ValidationError(LEDGER_FROZEN)

    def delete(self):
        raise ValidationError(LEDGER_FROZEN)

    def bulk_update(self, objs, fields, *args, **kwargs):
        raise ValidationError(LEDGER_FROZEN)


class LedgerEntry(models.Model):
    """
    One line of a trade customer's account (G47), append-only: what they owe is the sum of their entries, never a
    stored balance. Positive = they owe more (an issued invoice, a debit adjustment); negative = they owe less (an
    issued credit note, a payment, a credit adjustment). Invoices and credit notes post themselves when issued;
    staff post adjustments with a reason. A mistake is corrected by a reversing adjustment.
    """

    INVOICE, CREDIT_NOTE, PAYMENT, ADJUSTMENT = 'invoice', 'credit_note', 'payment', 'adjustment'
    KINDS = [(INVOICE, 'Invoice'), (CREDIT_NOTE, 'Credit note'), (PAYMENT, 'Payment'), (ADJUSTMENT, 'Adjustment')]
    objects = LedgerEntryQuerySet.as_manager()

    customer = models.ForeignKey(TradeCustomer, on_delete=models.PROTECT, related_name='ledger_entries')
    kind = models.CharField(max_length=12, choices=KINDS)
    entry_date = models.DateField(help_text="The document's date (UK); an adjustment's posting date.")
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text='Signed: positive = owed more.')
    invoice = models.OneToOneField(Invoice, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='ledger_entry')
    credit_note = models.OneToOneField(CreditNote, on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='ledger_entry')
    payment_ref = models.CharField(max_length=60, blank=True)  # G49
    description = models.CharField(max_length=120)
    reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['customer', 'entry_date', 'id']
        indexes = [models.Index(fields=['customer', 'entry_date'], name='ledger_customer_date')]
        constraints = [
            models.CheckConstraint(condition=~Q(amount=0), name='ledger_amount_not_zero'),
            models.CheckConstraint(
                condition=(
                    Q(kind='invoice', amount__gt=0, invoice__isnull=False, credit_note__isnull=True)
                    | Q(kind='credit_note', amount__lt=0, credit_note__isnull=False, invoice__isnull=True)
                    | Q(kind='payment', amount__lt=0, invoice__isnull=True, credit_note__isnull=True)
                    | (Q(kind='adjustment', invoice__isnull=True, credit_note__isnull=True) & ~Q(reason=''))
                ),
                name='ledger_kind_sign_and_document',
            ),
        ]

    def __str__(self):
        return f'{self.description} {self.amount}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(LEDGER_FROZEN)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(LEDGER_FROZEN)


@receiver(pre_delete, sender=LedgerEntry)
def _ledger_entries_are_kept(sender, instance, **kwargs):
    raise ValidationError(LEDGER_FROZEN)
