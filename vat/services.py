# vat/services.py

"""VAT arithmetic. Till prices include VAT; a line's VAT is taken out of its rounded gross and rounded once."""

from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import VatRate

UK = ZoneInfo('Europe/London')

PENNY = Decimal('0.01')
HUNDRED = Decimal('100')


class NoVatRate(Exception):
    """No rate is in force for this code on this date (the rate table needs a row)."""


def uk_date():
    """Today in the UK. Rates change at UK midnight, which is 23:00 UTC in summer (the server runs on UTC)."""
    return timezone.localtime(timezone.now(), UK).date()


def rate_for(code, on):
    """The percentage in force for ``code`` on the date ``on``."""
    row = VatRate.objects.filter(code=code, valid_from__lte=on).order_by('-valid_from').first()
    if row is None:
        raise NoVatRate(f'No VAT rate for {code!r} on {on}.')
    return row.rate


def split_inclusive(gross, rate):
    """``(net, vat)`` for a VAT-inclusive ``gross`` at ``rate`` percent. VAT is rounded half-up to the penny."""
    gross = Decimal(gross).quantize(PENNY, rounding=ROUND_HALF_UP)
    vat = (gross * rate / (HUNDRED + rate)).quantize(PENNY, rounding=ROUND_HALF_UP)
    return gross - vat, vat
