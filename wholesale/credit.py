"""
Credit control (G48). An order is blocked from confirm, start-pick and the pick list when:

- ``on_hold``: the customer is on hold (by hand, with ``hold_reason``) — lifted only by taking them off hold;
- ``over_limit``: exposure (ledger balance + the estimated gross of their confirmed-to-delivered orders not yet
  invoiced, this one included) is above ``credit_limit`` (blank: no limit);
- ``overdue``: an invoice is still partly open more than N days (company ``Setting`` ``credit_overdue_days``,
  default 7) after its due date. Until payments are allocated (G49), credits pay the oldest debits first.

A staff ``CreditOverride`` with a reason lifts ``over_limit`` and ``overdue`` for that one order.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum

from common.services.settings_service import get_company_setting
from vat.services import uk_date

from .ledger import balance
from .models import CreditOverride, Invoice, LedgerEntry, SalesOrder, TradeCustomer
from .orders import OrderStateError

ZERO = Decimal('0.00')
DAYS_KEY, DEFAULT_DAYS = 'credit_overdue_days', 7
OPEN_STATUSES = [SalesOrder.CONFIRMED, SalesOrder.PICKING, SalesOrder.PICKED, SalesOrder.DISPATCHED,
                 SalesOrder.DELIVERED]
OVERRIDABLE = {'over_limit', 'overdue'}


class CreditError(OrderStateError):
    """An override that can't be made."""


class CreditBlocked(CreditError):
    def __init__(self, reasons):
        super().__init__(' '.join(reason['message'] for reason in reasons))
        self.reasons = reasons


def overdue_days(company_id):
    raw = get_company_setting(company_id, DAYS_KEY, str(DEFAULT_DAYS))
    return int(raw) if raw.isdigit() else DEFAULT_DAYS  # a typo must neither stop all orders nor wave them through


def open_invoices(customer, today=None):
    """The invoices still partly open, oldest first: credits pay off debits (invoices and debit adjustments) in
    entry order. ``[(invoice entry, open amount)]``."""
    entries = list(LedgerEntry.objects.filter(customer=customer).select_related('invoice').order_by('entry_date',
                                                                                                    'id'))
    paid = -sum((entry.amount for entry in entries if entry.amount < 0), ZERO)
    still_open = []
    for entry in (entry for entry in entries if entry.amount > 0):
        covered = min(paid, entry.amount)
        paid -= covered
        if entry.amount > covered and entry.invoice_id:
            still_open.append((entry.invoice, entry.amount - covered))
    return still_open


def _open_orders(customer, leave_out=None):
    orders = SalesOrder.objects.filter(customer=customer, status__in=OPEN_STATUSES).exclude(
        invoice__status=Invoice.ISSUED)  # an issued invoice is in the balance already
    if leave_out is not None:
        orders = orders.exclude(pk=leave_out)
    return (orders.aggregate(total=Sum('estimated_gross'))['total'] or ZERO).quantize(ZERO)


def status(customer, order=None):
    """Balance, open orders, exposure (with ``order``'s estimate), limit, available and overdue invoices."""
    today = uk_date()
    grace = overdue_days(customer.company_id)
    owed = balance(customer)
    pending = _open_orders(customer, leave_out=order.pk if order else None)
    exposure = owed + pending + ((order.estimated_gross or ZERO) if order else ZERO)
    late = [{'invoice': invoice.pk, 'number': invoice.number_display, 'due_date': invoice.due_date.isoformat(),
             'open': f'{amount:.2f}'}
            for invoice, amount in open_invoices(customer)
            if invoice.due_date + timedelta(days=grace) < today]
    limit = customer.credit_limit
    return {'balance': owed, 'open_orders': pending, 'exposure': exposure, 'credit_limit': limit,
            'available': None if limit is None else limit - exposure, 'overdue': late, 'overdue_days': grace}


def blocks(customer, order=None):
    """What stops ``order`` (or a new order of £0) now, before any override: a list of reasons."""
    report = status(customer, order)
    reasons = []
    if customer.status == TradeCustomer.ON_HOLD:
        why = f' ({customer.hold_reason})' if customer.hold_reason else ''
        reasons.append({'code': 'on_hold', 'message': f'The customer is on hold{why}.'})
    limit = report['credit_limit']
    if limit is not None and report['exposure'] > limit:
        reasons.append({'code': 'over_limit', 'exposure': f'{report["exposure"]:.2f}', 'credit_limit': f'{limit:.2f}',
                        'message': f'Over the credit limit: £{report["exposure"]:,.2f} owed and on order against '
                                   f'£{limit:,.2f}.'})
    if report['overdue']:
        numbers = ', '.join(f'{row["number"]} (£{row["open"]} due {row["due_date"]})' for row in report['overdue'])
        reasons.append({'code': 'overdue', 'invoices': report['overdue'],
                        'message': f'Overdue more than {report["overdue_days"]} days: {numbers}.'})
    return reasons, report


def _overridden(order):
    return order.pk is not None and CreditOverride.objects.filter(order_id=order.pk).exists()


def order_blocks(order, customer=None):
    """What stops this order now, after its override (which lifts the limit and overdue rules)."""
    customer = customer or TradeCustomer.objects.get(pk=order.customer_id)
    reasons, _report = blocks(customer, order)
    if _overridden(order):
        reasons = [reason for reason in reasons if reason['code'] not in OVERRIDABLE]
    return reasons


def check(order, customer=None):
    """Raise ``CreditBlocked`` when something stops this order."""
    reasons = order_blocks(order, customer)
    if reasons:
        raise CreditBlocked(reasons)


def override(order, user, reason, customer=None):
    """Staff let ``order`` past its limit/overdue blocks, with a reason; logged with what blocked it."""
    if not getattr(user, 'is_staff', False):
        raise CreditError('Only staff override a credit block.')
    reason = (reason or '').strip()
    if not reason:
        raise CreditError('Give a reason for the override.')
    if len(reason) > 255:
        raise CreditError('The reason is at most 255 characters.')
    customer = customer or TradeCustomer.objects.get(pk=order.customer_id)
    reasons, report = blocks(customer, order)
    if any(item['code'] == 'on_hold' for item in reasons):
        raise CreditError('The customer is on hold: take them off hold to go on (an override does not lift it).')
    lifted = [item['code'] for item in reasons if item['code'] in OVERRIDABLE]
    if not lifted:
        raise CreditError('Nothing blocks this order on credit.')
    return CreditOverride.objects.create(
        order=order, reason=reason, blocks=lifted, balance=report['balance'], exposure=report['exposure'],
        credit_limit=report['credit_limit'], overdue=report['overdue'], created_by=user)
