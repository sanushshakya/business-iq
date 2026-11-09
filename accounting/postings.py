"""
Automatic postings (G53): what the business already records, posted to the journal (G52) once each.

- Till takings per branch per UK day (sales by when they reached the server): Dr Cash / Card clearing, Cr sales by
  VAT code and VAT on sales; voids per branch per day (by when voided): the opposite.
- Customer ledger entries (G47/G49): invoices and credit notes by their lines, payments and their reversals by
  method, adjustments against Suspense; so Trade debtors always equals the customer ledger.
- Write-offs and butchery loss at cost (periodic stock): Dr Stock wastage / Butchery yield loss, Cr Purchases.

Each has a ``source`` and ``source_ref``, so running a day again posts nothing new. A source dated in a locked month
is posted on the first open day, saying its own date. Supplier bills wait for purchase orders (parked).
"""

import logging
from collections import defaultdict, namedtuple
from datetime import datetime, time, timedelta
from decimal import Decimal

from celery import shared_task
from django.db.models import Q

from inventory.models import Breakdown, WriteOff
from sales.models import Sale, SaleItem
from tenants.models import Company
from vat.services import UK, uk_date
from wholesale.models import LedgerEntry, Payment

from .journal import JournalError, locked_through, post_journal
from .models import Account, Journal

logger = logging.getLogger(__name__)

ZERO = Decimal('0.00')
Posting = namedtuple('Posting', 'source ref date narrative lines')  # lines: [(system key, debit, credit, text)]
SALES_KEYS = {'zero': 'sales_zero', 'reduced': 'sales_reduced', 'standard': 'sales_standard',
              'exempt': 'sales_exempt'}
PAYMENT_KEYS = {Payment.CASH: 'cash', Payment.CARD: 'card_clearing', Payment.BANK_TRANSFER: 'bank',
                Payment.CHEQUE: 'bank'}
KEYS = {'cash', 'card_clearing', 'bank', 'debtors', 'vat_output', 'suspense', 'wastage', 'yield_loss',
        'purchases', *SALES_KEYS.values()}


class MissingAccounts(Exception):
    pass


def _day_range(day):
    start = datetime.combine(day, time.min, tzinfo=UK)
    return start, start + timedelta(days=1)


def _uk_day(moment):
    return moment.astimezone(UK).date()


def _side(lines, key, amount, text=''):
    """A positive ``amount`` is a debit, a negative one a credit; 0 adds nothing."""
    if amount > 0:
        lines.append((key, amount, ZERO, text))
    elif amount < 0:
        lines.append((key, ZERO, -amount, text))


def _sales_lines(sales, sign):
    """Lines for ``sales`` (sign 1: takings, -1: voids); and whether any line had no VAT recorded."""
    takings, nets, vat, unrecorded = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO), ZERO, False
    for method, code, gross, net, line_vat in SaleItem.objects.filter(sale__in=sales).values_list(
            'sale__payment_method', 'vat_code', 'line_total', 'net_amount', 'vat_amount'):
        takings['cash' if method == Sale.CASH else 'card_clearing'] += gross
        if net is None or line_vat is None or code not in SALES_KEYS:
            nets['suspense'] += gross
            unrecorded = True
        else:
            nets[SALES_KEYS[code]] += net
            vat += line_vat
    lines = []
    for key, amount in sorted(takings.items()):
        _side(lines, key, sign * amount)
    for key, amount in sorted(nets.items()):
        _side(lines, key, -sign * amount)
    _side(lines, 'vat_output', -sign * vat)
    return lines, unrecorded


def till_day(branch, day):
    start, end = _day_range(day)
    sales = Sale.objects.filter(session__till__branch=branch, created_at__gte=start, created_at__lt=end)
    lines, unrecorded = _sales_lines(sales, 1)
    if not lines:
        return None
    note = ' (VAT not recorded on some lines: in Suspense)' if unrecorded else ''
    return Posting('till', f'{branch.pk}:{day}', day, f'Till takings {branch.name} {day:%d/%m/%Y}{note}', lines)


def till_voids(branch, day):
    start, end = _day_range(day)
    sales = Sale.objects.filter(session__till__branch=branch, status=Sale.VOIDED, voided_at__gte=start,
                                voided_at__lt=end)
    lines, _unrecorded = _sales_lines(sales, -1)
    if not lines:
        return None
    return Posting('till_void', f'{branch.pk}:{day}', day, f'Till voids {branch.name} {day:%d/%m/%Y}', lines)


def _document_lines(document, debtors_sign, label):
    """An invoice (debtors_sign 1) or credit note (-1): debtors gross; sales by VAT code; VAT; a difference between
    the stored gross and the lines (never expected) to Suspense."""
    lines, nets, vat = [], defaultdict(lambda: ZERO), ZERO
    for code, net, line_vat in document.lines.values_list('vat_code', 'net', 'vat'):
        nets[SALES_KEYS.get(code, 'suspense')] += net
        vat += line_vat
    _side(lines, 'debtors', debtors_sign * document.gross_total, label)
    for key, amount in sorted(nets.items()):
        _side(lines, key, -debtors_sign * amount)
    _side(lines, 'vat_output', -debtors_sign * vat)
    difference = debtors_sign * (document.gross_total - sum(nets.values()) - vat)
    if difference:
        logger.warning('%s: gross differs from its lines by %s; difference to Suspense', label, difference)
        _side(lines, 'suspense', -difference)
    return lines


def ledger_entry(entry):
    kind, amount = entry.kind, entry.amount
    if kind == LedgerEntry.INVOICE:
        label = f'Invoice {entry.invoice.number_display}'
        lines = _document_lines(entry.invoice, 1, label)
    elif kind == LedgerEntry.CREDIT_NOTE:
        label = f'Credit note {entry.credit_note.number_display}'
        lines = _document_lines(entry.credit_note, -1, label)
    elif kind in (LedgerEntry.PAYMENT, LedgerEntry.PAYMENT_REVERSAL):
        label = f'{entry.description} {entry.customer.account_code}'
        lines = []
        _side(lines, PAYMENT_KEYS[entry.payment.method], -amount)
        _side(lines, 'debtors', amount)
    else:  # an adjustment: the accountant reclassifies it from Suspense
        label = f'Customer adjustment {entry.customer.account_code}: {entry.reason}'
        lines = []
        _side(lines, 'debtors', amount)
        _side(lines, 'suspense', -amount)
    return Posting('ledger', str(entry.pk), entry.entry_date, label[:200], lines)


def write_off(item):
    lines = []
    _side(lines, 'wastage', item.cost)
    _side(lines, 'purchases', -item.cost)
    return Posting('write_off', str(item.pk), _uk_day(item.written_off_at),
                   f'Write-off {item.reference} ({item.get_reason_display()}) at cost', lines)


def _loss(item):
    allocated = sum((line.allocated_cost or ZERO) for line in item.lines.all() if line.kind == 'product')
    return item.input_cost - allocated


def breakdown(item, void=False):
    loss = _loss(item)
    if loss <= 0:
        return None
    sign = -1 if void else 1
    lines = []
    _side(lines, 'yield_loss', sign * loss)
    _side(lines, 'purchases', -sign * loss)
    if void:
        return Posting('breakdown_void', str(item.pk), _uk_day(item.voided_at),
                       f'Breakdown {item.reference} voided: loss at cost reversed', lines)
    return Posting('breakdown', str(item.pk), _uk_day(item.created_at),
                   f'Breakdown {item.reference}: waste and loss at cost', lines)


def _posted(company, source):
    return set(Journal.objects.filter(company=company, source=source).values_list('source_ref', flat=True))


def _sources(company, start, end):
    """Every posting for ``start``–``end``, plus earlier documents never posted; and how many were skipped."""
    found, skipped = [], 0
    day = start
    branches = list(company.branches.all())
    while day <= end:
        for branch in branches:
            found += [till_day(branch, day), till_voids(branch, day)]
        day += timedelta(days=1)
    _first, until = _day_range(start)[0], _day_range(end)[1]

    posted = _posted(company, 'ledger')
    for entry in (LedgerEntry.objects.filter(customer__company=company, entry_date__lte=end)
                  .select_related('invoice', 'credit_note', 'payment', 'customer').order_by('entry_date', 'id')):
        if entry.entry_date >= start or str(entry.pk) not in posted:
            found.append(ledger_entry(entry))

    posted = _posted(company, 'write_off')
    for item in WriteOff.objects.filter(company=company, status=WriteOff.WRITTEN_OFF, written_off_at__lt=until):
        if _uk_day(item.written_off_at) >= start or str(item.pk) not in posted:
            if item.cost is None:
                skipped += 1
            else:
                found.append(write_off(item))

    posted, posted_voids = _posted(company, 'breakdown'), _posted(company, 'breakdown_void')
    candidates = (Breakdown.objects.filter(company=company, created_at__lt=until).prefetch_related('lines')
                  .filter(Q(created_at__gte=_first) | Q(voided_at__gte=_first)
                          | ~Q(pk__in=[int(ref) for ref in posted])
                          | (Q(voided_at__isnull=False) & ~Q(pk__in=[int(ref) for ref in posted_voids]))))
    for item in candidates:
        if item.input_cost is None:
            skipped += _uk_day(item.created_at) >= start
            continue
        if _uk_day(item.created_at) >= start or str(item.pk) not in posted:
            found.append(breakdown(item))
        voided = item.voided_at is not None and item.voided_at < until
        if voided and (_uk_day(item.voided_at) >= start or str(item.pk) not in posted_voids):
            found.append(breakdown(item, void=True))
    return [posting for posting in found if posting is not None], skipped


def _accounts(company):
    accounts = {account.system_key: account for account in Account.objects.filter(
        company=company, system_key__in=KEYS)}
    missing = KEYS - set(accounts)
    if missing:
        raise MissingAccounts(f'No account for {", ".join(sorted(missing))} (system keys).')
    return accounts


def _first_open_day(company):
    last = locked_through(company)
    return None if last is None else (last.replace(day=28) + timedelta(days=4)).replace(day=1)


def run_postings(company, start, end, user=None):
    """Post everything for ``start``–``end`` (UK days) not yet posted. Returns ``{posted, already, moved, skipped,
    conflicts, error}``; never raises for one bad source."""
    summary = {'posted': 0, 'already': 0, 'moved': 0, 'skipped': 0, 'conflicts': [], 'error': ''}
    try:
        accounts = _accounts(company)
    except MissingAccounts as error:
        summary['error'] = str(error)
        return summary
    found, summary['skipped'] = _sources(company, start, end)
    first_open = _first_open_day(company)
    for posting in found:
        day, narrative = posting.date, posting.narrative
        if first_open is not None and day < first_open:
            day, narrative = first_open, f'{narrative} (dated {posting.date:%d/%m/%Y}, a locked month)'
        lines = [{'account': accounts[key], 'debit': debit, 'credit': credit, 'description': text}
                 for key, debit, credit, text in posting.lines]
        try:
            journal = post_journal(company, date=day, narrative=narrative[:255], lines=lines, user=user,
                                   source=posting.source, source_ref=posting.ref)
        except JournalError as error:
            logger.warning('Posting %s %s refused: %s', posting.source, posting.ref, error.detail)
            summary['conflicts'].append({'source': posting.source, 'ref': posting.ref, 'detail': error.detail})
            continue
        if journal.repeated:
            summary['already'] += 1
        else:
            summary['posted'] += 1
            summary['moved'] += day != posting.date
    return summary


@shared_task
def run_nightly():
    """After midnight (beat): yesterday for every company; one company's problem never stops the others."""
    yesterday = uk_date() - timedelta(days=1)
    for company in Company.objects.order_by('pk'):
        try:
            summary = run_postings(company, yesterday, yesterday)
        except Exception:  # noqa: BLE001
            logger.exception('Postings failed for company %s', company.pk)
            continue
        if summary['error'] or summary['conflicts']:
            logger.warning('Postings for company %s: %s; %d conflicts', company.pk, summary['error'] or 'ok',
                           len(summary['conflicts']))
