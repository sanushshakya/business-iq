"""
B2B wholesale (M7). G38: trade customers — the restaurants and shops that buy on account, with their delivery
addresses, contacts and the days they take deliveries. G39: customer groups and price lists (ex VAT, with quantity
breaks and dates; resolved in ``pricing.py``). Orders (G40+) and credit build on these.

Contacts and addresses are business details but may name people: never log them, and use obviously fake data in
tests.
"""

import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from vat.models import VAT_CODES

WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
ACCOUNT_CODE = re.compile(r'[A-Z0-9][A-Z0-9-]{0,19}', re.ASCII)
# UK VAT numbers: GB (or XI for Northern Ireland) + 9 digits, or 12 for a group/branch; GBGD/GBHA + 3 for
# government bodies and health authorities.
VAT_NUMBER = re.compile(r'(GB|XI)(\d{9}|\d{12})|GB(GD|HA)\d{3}', re.ASCII)
POSTCODE = re.compile(r'[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}', re.ASCII)


def normalise_account_code(value):
    return (value or '').strip().upper()


def account_code_problem(code):
    if not ACCOUNT_CODE.fullmatch(code):
        return 'Use 1-20 capital letters, digits or dashes, starting with a letter or digit.'
    return None


def normalise_vat_number(value):
    return re.sub(r'[\s.-]', '', value or '').upper()


def vat_number_problem(number):
    if number and not VAT_NUMBER.fullmatch(number):
        return 'Not a UK VAT number (GB or XI and 9 or 12 digits).'
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

    class Meta:
        ordering = ['order', 'id']
        constraints = [models.UniqueConstraint(fields=['order', 'product'], name='one_line_per_product_per_order')]

    def __str__(self):
        return f'{self.product} x {self.weight_kg if self.quantity is None else self.quantity}'
