"""
Aged debtors (G50): what each trade customer owes as at the end of a day (UK), split by how long it is past due.

Open debits (invoices by their due date; debit adjustments and payment reversals by their own date) go in
``current`` (not yet due) or 1–30 / 31–60 / 61–90 / 90+ days past due; credits nothing pays yet (payments, credit
notes, credit adjustments) are ``on_account``, negative. ``total`` is their sum, which is always the ledger balance
on that day: allocating only decides what is open.

As at a past day, an entry counts if it is dated on or before it, and an allocation counts from the latest of its
two entries' dates and the day it was made, up to the day it was released. So a payment dated back but allocated
today was on account until today. (Allocations made by the G49 migrations count from the day they ran.)
"""

from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from vat.services import UK

from .models import Allocation, LedgerEntry, TradeCustomer

ZERO = Decimal('0.00')
BUCKETS = ('current', 'days_1_30', 'days_31_60', 'days_61_90', 'days_90_plus')
COLUMNS = BUCKETS + ('on_account', 'total')


def _bucket(days_late):
    if days_late <= 0:
        return 'current'
    if days_late <= 30:
        return 'days_1_30'
    if days_late <= 60:
        return 'days_31_60'
    if days_late <= 90:
        return 'days_61_90'
    return 'days_90_plus'


def _uk_day(moment):
    return timezone.localtime(moment, UK).date()


def _allocated(company_id, as_at, customer_id=None):
    """{entry id: what active allocations take from it as at ``as_at``}, on both sides."""
    taken = defaultdict(lambda: ZERO)
    rows = Allocation.objects.filter(debit__customer__company_id=company_id, debit__entry_date__lte=as_at,
                                     credit__entry_date__lte=as_at)
    if customer_id is not None:
        rows = rows.filter(debit__customer_id=customer_id)
    for credit_id, debit_id, amount, created_at, released_at, credit_date, debit_date in rows.values_list(
            'credit_id', 'debit_id', 'amount', 'created_at', 'released_at', 'credit__entry_date', 'debit__entry_date'):
        if max(credit_date, debit_date, _uk_day(created_at)) > as_at:
            continue
        if released_at is not None and _uk_day(released_at) <= as_at:
            continue
        taken[credit_id] += amount
        taken[debit_id] += amount
    return taken


def _entries(company_id, as_at, customer_id=None):
    """Every entry dated on or before ``as_at`` with what was still open of it then (a credit's: negative)."""
    taken = _allocated(company_id, as_at, customer_id)
    entries = LedgerEntry.objects.filter(customer__company_id=company_id, entry_date__lte=as_at)
    if customer_id is not None:
        entries = entries.filter(customer_id=customer_id)
    entries = entries.select_related('invoice').only(
        'id', 'customer_id', 'kind', 'amount', 'entry_date', 'description', 'invoice__id', 'invoice__due_date',
        'invoice__number')
    for entry in entries.order_by('entry_date', 'id'):
        sign = 1 if entry.amount > 0 else -1
        yield entry, entry.amount - sign * taken[entry.pk]


def due_date(entry):
    return entry.invoice.due_date if entry.invoice_id and entry.invoice.due_date else entry.entry_date


def open_items(customer, as_at):
    """``[(entry, open)]``: the customer's invoices and charges still (partly) open at the end of ``as_at``, oldest
    first, as the statement lists them."""
    return [(entry, still) for entry, still in _entries(customer.company_id, as_at, customer.pk)
            if entry.amount > 0 and still > 0]


def report(company_id, as_at, customer=None):
    """``{as_at, customers: [{customer, account_code, name, current…days_90_plus, on_account, total}], totals}``:
    customers owing or owed something, by account code (only ``customer`` when given)."""
    rows = defaultdict(lambda: dict.fromkeys(COLUMNS, ZERO))
    for entry, still in _entries(company_id, as_at, customer.pk if customer is not None else None):
        row = rows[entry.customer_id]
        if entry.amount > 0:
            if still:
                row[_bucket((as_at - due_date(entry)).days)] += still
        else:
            row['on_account'] += still
        row['total'] += entry.amount
    names = {pk: (code, trading or legal) for pk, code, trading, legal in TradeCustomer.objects.filter(
        pk__in=list(rows)).values_list('pk', 'account_code', 'trading_name', 'legal_name')}
    customers = [
        {'customer': pk, 'account_code': names[pk][0], 'name': names[pk][1], **row}
        for pk, row in rows.items() if any(row[name] for name in COLUMNS)
    ]
    customers.sort(key=lambda row: (row['account_code'], row['customer']))
    totals = {name: sum((row[name] for row in customers), ZERO) for name in COLUMNS}
    return {'as_at': as_at, 'customers': customers, 'totals': totals}
