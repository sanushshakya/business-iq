"""
The nine-box VAT return (G54), standard accounting, from the journal by journal date. Box 1: VAT on sales (net credit
on ``vat_output``); box 4: VAT on purchases (net debit on ``vat_input``); box 6: sales ex VAT (the four sales accounts);
box 7: purchases ex VAT (accounts marked ``vat_purchases``, without G53's waste and loss reclassifications); boxes 2, 8
and 9 are 0 (GB only). Boxes 6-9 are whole pounds, pence dropped. Filing stores the boxes and locks the months.
"""

from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.db.models import F, Sum

from tenants.models import Company
from vat.services import uk_date

from .journal import lock_period
from .models import JournalLine, VatReturn

ZERO = Decimal('0.00')
POUND = Decimal('1')
SALES_KEYS = ['sales_standard', 'sales_reduced', 'sales_zero', 'sales_exempt']
RECLASSIFICATIONS = ['write_off', 'breakdown', 'breakdown_void']


class VatReturnError(ValueError):
    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def _net(lines):
    """Debits less credits."""
    return (lines.aggregate(total=Sum(F('debit') - F('credit')))['total'] or ZERO).quantize(Decimal('0.01'))


def _pounds(amount):
    return amount.quantize(POUND, ROUND_DOWN)


def vat_boxes(company, start, end):
    lines = JournalLine.objects.filter(journal__company=company, journal__date__gte=start, journal__date__lte=end)
    box1 = -_net(lines.filter(account__system_key='vat_output'))
    box2 = ZERO
    box3 = box1 + box2
    box4 = _net(lines.filter(account__system_key='vat_input'))
    net = box3 - box4
    box6 = _pounds(-_net(lines.filter(account__system_key__in=SALES_KEYS)))
    box7 = _pounds(_net(lines.filter(account__vat_purchases=True).exclude(journal__source__in=RECLASSIFICATIONS)))
    return {'start': start, 'end': end, 'box1': box1, 'box2': box2, 'box3': box3, 'box4': box4, 'box5': abs(net),
            'net_direction': VatReturn.PAY if net >= 0 else VatReturn.RECLAIM, 'box6': box6, 'box7': box7,
            'box8': Decimal('0'), 'box9': Decimal('0')}


def _month_end(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


@transaction.atomic
def file_return(company, start, end, user):
    """Store the return for ``start``–``end`` (whole months that have ended, not overlapping a filed one) as it is now,
    and lock its months so nothing changes it."""
    if start.day != 1 or end != _month_end(end) or start > end:
        raise VatReturnError('A return covers whole months: from a 1st to a month end.')
    if end >= uk_date().replace(day=1):
        raise VatReturnError('Only months that have ended are filed.')
    Company.objects.select_for_update().filter(pk=company.pk).first()
    if VatReturn.objects.filter(company=company, period_start__lte=end, period_end__gte=start).exists():
        raise VatReturnError('These months are in a return filed already.')
    boxes = vat_boxes(company, start, end)
    filed = VatReturn.objects.create(company=company, period_start=start, period_end=end, filed_by=user,
                                     **{key: value for key, value in boxes.items() if key not in ('start', 'end')})
    lock_period(company, end.replace(day=1), user)
    return filed
