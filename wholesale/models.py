"""
B2B wholesale (M7). G38: trade customers — the restaurants and shops that buy on account, with their delivery
addresses, contacts and the days they take deliveries. Price lists (G39), orders (G40+) and credit build on these.

Contacts and addresses are business details but may name people: never log them, and use obviously fake data in
tests.
"""

import re

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

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
