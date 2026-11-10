"""
Automatic postings (G53): what the business already records, posted to the journal (G52) once each.

- Till takings per branch per UK day (sales by when they reached the server): Dr Cash / Card clearing, Cr sales by
  VAT code and VAT on sales; each void: the opposite.
- Customer ledger entries (G47/G49): invoices and credit notes by their lines, payments and their reversals by
  method, adjustments against Suspense; so Trade debtors always equals the customer ledger.
- Write-offs and butchery loss at cost (periodic stock): Dr Stock wastage / Butchery yield loss, Cr Purchases.

Each has a ``source`` and ``source_ref``, so running a day again posts nothing new. A source dated in a locked month
is posted on the first open day, saying its own date. Supplier bills wait for purchase orders (parked). Voids post
one per sale (``till_void``, the sale id), on the later of the sale's and the void's day, and are caught up like
documents, so a void made offline and synced later is never lost.
"""

import logging
from collections import defaultdict, namedtuple
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from celery import shared_task
from django.db.models import CharField, Exists, OuterRef, Q
from django.db.models.functions import Cast

from inventory.models import Breakdown, WriteOff
from sales.models import Sale, SaleItem
from tenants.models import Company
from vat.services import UK, uk_date
from wholesale.models import LedgerEntry, Payment

from .journal import JournalError, locked_through, post_journal
from .models import Account, Journal

logger = logging.getLogger(__name__)

ZERO = Decimal('0.00')
PENNY = Decimal('0.01')
NIGHTLY_DAYS = 7  # the nightly run looks again at the last week's till days, in case a night was missed
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


def void_day(sale):
    """A void posts on the later of its sale's day and its void's day: a sale voided on the till while offline
    reaches the server (and the day's takings) later than its ``voided_at``."""
    return max(_uk_day(sale.created_at), _uk_day(sale.voided_at))


def till_void(sale):
    lines, _unrecorded = _sales_lines(Sale.objects.filter(pk=sale.pk), -1)
    if not lines:
        return None
    branch = sale.session.till.branch
    return Posting('till_void', str(sale.pk), void_day(sale),
                   f'Void of sale {sale.receipt_number} ({branch.name}), sold {_uk_day(sale.created_at):%d/%m/%Y}',
                   lines)


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
        label = f'Customer adjustment {entry.customer.account_code} (ledger entry {entry.pk})'
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
    """The input's cost per kg × the kg that did not become product (waste and loss), to the penny. (G22 spreads the
    whole input cost over the products' batches; this is the share of it the waste and loss stand for.)"""
    product_kg = sum((line.weight_kg for line in item.lines.all() if line.kind == 'product'), Decimal('0'))
    lost_kg = item.input_kg - product_kg
    if not item.input_kg or lost_kg <= 0:
        return ZERO
    return (item.input_cost * lost_kg / item.input_kg).quantize(PENNY, ROUND_HALF_UP)


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


def _not_posted(company, source):
    return ~Exists(Journal.objects.filter(company=company, source=source,
                                          source_ref=Cast(OuterRef('pk'), output_field=CharField())))


def _sources(company, start, end):
    """``[(builder, row)]`` for ``start``–``end`` plus every earlier source never posted (found in SQL, so the
    nightly run does not read all history); and how many in the range have no cost to post."""
    found, skipped = [], 0
    first, until = _day_range(start)[0], _day_range(end)[1]
    day = start
    branches = list(company.branches.all())
    while day <= end:
        found += [(till_day, (branch, day)) for branch in branches]
        day += timedelta(days=1)

    voided = (Sale.objects.filter(session__till__branch__company=company, status=Sale.VOIDED, voided_at__lt=until,
                                  created_at__lt=until)
              .filter(Q(voided_at__gte=first) | Q(created_at__gte=first) | _not_posted(company, 'till_void'))
              .select_related('session__till__branch').order_by('voided_at', 'pk'))
    found += [(till_void, (sale,)) for sale in voided]

    entries = (LedgerEntry.objects.filter(customer__company=company, entry_date__lte=end)
               .filter(Q(entry_date__gte=start) | _not_posted(company, 'ledger'))
               .select_related('invoice', 'credit_note', 'payment', 'customer').order_by('entry_date', 'id'))
    found += [(ledger_entry, (entry,)) for entry in entries]

    write_offs = WriteOff.objects.filter(company=company, status=WriteOff.WRITTEN_OFF, written_off_at__lt=until)
    skipped += write_offs.filter(written_off_at__gte=first, cost__isnull=True).count()
    found += [(write_off, (item,)) for item in write_offs.filter(cost__isnull=False).filter(
        Q(written_off_at__gte=first) | _not_posted(company, 'write_off')).order_by('written_off_at', 'pk')]

    breakdowns = Breakdown.objects.filter(company=company, created_at__lt=until)
    skipped += breakdowns.filter(created_at__gte=first, input_cost__isnull=True).count()
    costed = (breakdowns.filter(input_cost__isnull=False).filter(Q(loss_kg__gt=0) | Q(lines__kind='waste'))
              .distinct().prefetch_related('lines').order_by('created_at', 'pk'))
    found += [(breakdown, (item,)) for item in costed.filter(
        Q(created_at__gte=first) | _not_posted(company, 'breakdown'))]
    found += [(breakdown, (item, True)) for item in costed.filter(voided_at__isnull=False, voided_at__lt=until).filter(
        Q(voided_at__gte=first) | _not_posted(company, 'breakdown_void'))]
    return found, skipped


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
    """Post everything for ``start``–``end`` (UK days, yesterday at the latest) not yet posted, and every earlier
    source never posted. Returns ``{posted, already, moved, skipped, conflicts, error}``; one bad source is a
    conflict, the rest carry on."""
    if end >= uk_date():
        raise ValueError('A day is posted once it is over: yesterday at the latest.')
    summary = {'posted': 0, 'already': 0, 'moved': 0, 'skipped': 0, 'conflicts': [], 'error': ''}
    try:
        accounts = _accounts(company)
    except MissingAccounts as error:
        summary['error'] = str(error)
        return summary
    found, summary['skipped'] = _sources(company, start, end)
    first_open = _first_open_day(company)
    for build, args in found:
        try:
            posting = build(*args)
            if posting is None:
                continue
            day, narrative = posting.date, posting.narrative
            if first_open is not None and day < first_open:
                day, narrative = first_open, f'{narrative} (dated {posting.date:%d/%m/%Y}, a locked month)'
            lines = [{'account': accounts[key], 'debit': debit, 'credit': credit, 'description': text}
                     for key, debit, credit, text in posting.lines]
            journal = post_journal(company, date=day, narrative=narrative[:255], lines=lines, user=user,
                                   source=posting.source, source_ref=posting.ref)
        except Exception as error:  # noqa: BLE001 - one bad source never stops the others
            source = getattr(build, '__name__', 'source')
            ref = getattr(args[0], 'pk', args[0])
            detail = error.detail if isinstance(error, JournalError) else f'{type(error).__name__} building it.'
            logger.warning('Posting %s %s refused: %s', source, ref, type(error).__name__)
            summary['conflicts'].append({'source': source, 'ref': str(ref), 'detail': detail})
            continue
        if journal.repeated:
            summary['already'] += 1
        else:
            summary['posted'] += 1
            summary['moved'] += day != posting.date
    return summary


@shared_task
def run_nightly():
    """After midnight (beat): the last week to yesterday for every company (days posted before are left as they are;
    a missed night is caught up); one company's problem never stops the others."""
    yesterday = uk_date() - timedelta(days=1)
    for company in Company.objects.order_by('pk'):
        try:
            summary = run_postings(company, yesterday - timedelta(days=NIGHTLY_DAYS - 1), yesterday)
        except Exception:  # noqa: BLE001
            logger.exception('Postings failed for company %s', company.pk)
            continue
        logger.info('Postings for company %s: %d posted, %d already', company.pk, summary['posted'],
                    summary['already'])
        if summary['error'] or summary['conflicts']:
            logger.warning('Postings for company %s: %s; %d conflicts', company.pk, summary['error'] or 'ok',
                           len(summary['conflicts']))
