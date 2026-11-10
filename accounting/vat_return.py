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

from .journal import EARLIEST, post_journal, lock_period
from .models import Account, JournalLine, VatReturn

ZERO = Decimal('0.00')
POUND = Decimal('1')
SALES_KEYS = ['sales_standard', 'sales_reduced', 'sales_zero', 'sales_exempt']
RECLASSIFICATIONS = ['write_off', 'breakdown', 'breakdown_void']
SETTLEMENT = 'vat_return'  # the journal filing posts: the period's VAT moved to "VAT due to HMRC"


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
    lines = (JournalLine.objects.filter(journal__company=company, journal__date__gte=start, journal__date__lte=end)
             .exclude(journal__source=SETTLEMENT)
             .exclude(journal__lines__account__system_key='vat_hmrc'))  # paying or settling HMRC is not a supply
    box1 = -_net(lines.filter(account__system_key='vat_output'))
    box2 = ZERO
    box3 = box1 + box2
    box4 = _net(lines.filter(account__system_key='vat_input'))
    net = box3 - box4
    box6 = _pounds(-_net(lines.filter(account__system_key__in=SALES_KEYS)))
    box7 = _pounds(_net(lines.filter(account__vat_purchases=True).exclude(journal__source__in=RECLASSIFICATIONS)
                        .exclude(journal__reverses__source__in=RECLASSIFICATIONS)))
    suspense = _net(lines.filter(account__system_key='suspense'))
    return {'start': start, 'end': end, 'box1': box1, 'box2': box2, 'box3': box3, 'box4': box4, 'box5': abs(net),
            'net_direction': VatReturn.PAY if net >= 0 else VatReturn.RECLAIM, 'box6': box6, 'box7': box7,
            'box8': Decimal('0'), 'box9': Decimal('0'), 'suspense': suspense}


def _month_end(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


class _Problems(Exception):
    pass


@transaction.atomic
def file_return(company, start, end, user):
    """File the return for ``start``–``end`` (whole months that have ended, not overlapping a filed one): post what
    is not posted yet for the period (refused if anything fails), store the boxes as they are, move the period's VAT
    to "VAT due to HMRC" (a ``vat_return`` journal on the last day) and lock the months so nothing changes them."""
    from .postings import run_postings

    if start.day != 1 or end != _month_end(end) or start > end:
        raise VatReturnError('A return covers whole months: from a 1st to a month end.')
    if start < EARLIEST:
        raise VatReturnError(f'Returns start from {EARLIEST:%B %Y}.')
    if end >= uk_date().replace(day=1):
        raise VatReturnError('Only months that have ended are filed.')
    Company.objects.select_for_update().filter(pk=company.pk).first()
    if VatReturn.objects.filter(company=company, period_start__lte=end, period_end__gte=start).exists():
        raise VatReturnError('These months are in a return filed already.')
    summary = run_postings(company, start, end, user=user)
    if summary['error'] or summary['conflicts']:
        problems = summary['error'] or '; '.join(f"{c['source']} {c['ref']}: {c['detail']}" for c in summary['conflicts'])
        raise VatReturnError(f'Some of the period could not be posted, so it is not filed: {problems}'[:500])
    boxes = vat_boxes(company, start, end)
    filed = VatReturn.objects.create(company=company, period_start=start, period_end=end, filed_by=user,
                                     **{key: boxes[key] for key in VAT_FIELDS})
    _settle(company, start, end, boxes, user)
    lock_period(company, end.replace(day=1), user)
    return filed


VAT_FIELDS = ['box1', 'box2', 'box3', 'box4', 'box5', 'net_direction', 'box6', 'box7', 'box8', 'box9']


def _settle(company, start, end, boxes, user):
    """Clear VAT on sales and on purchases for the period into "VAT due to HMRC" (paid or repaid from there)."""
    accounts = {account.system_key: account for account in Account.objects.filter(
        company=company, system_key__in=['vat_output', 'vat_input', 'vat_hmrc'])}
    lines = []
    if boxes['box1']:
        lines.append({'account': accounts['vat_output'], 'debit': max(boxes['box1'], ZERO),
                      'credit': max(-boxes['box1'], ZERO)})
    if boxes['box4']:
        lines.append({'account': accounts['vat_input'], 'debit': max(-boxes['box4'], ZERO),
                      'credit': max(boxes['box4'], ZERO)})
    net = boxes['box1'] - boxes['box4']
    if net:
        lines.append({'account': accounts['vat_hmrc'], 'debit': max(-net, ZERO), 'credit': max(net, ZERO)})
    if len(lines) >= 2:
        post_journal(company, date=end, narrative=f'VAT return {start:%d/%m/%Y} to {end:%d/%m/%Y}', lines=lines,
                     user=user, source=SETTLEMENT, source_ref=f'{start}:{end}')
