"""
The customer ledger (G47): what a trade customer owes is the sum of their ``LedgerEntry`` rows. Issued invoices and
credit notes post here inside their issue transaction (one entry per document); staff post adjustments with a reason.
Nothing here changes or deletes an entry.
"""

from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Sum

from vat.services import uk_date

from .models import LedgerEntry

ZERO = Decimal('0.00')
ADJUSTMENT_LIMIT = Decimal('999999.99')


class LedgerError(ValueError):
    pass


class AdjustmentConflict(LedgerError):
    """The ``client_ref`` was already used for a different adjustment."""


def post_invoice(invoice, customer_id):
    if not invoice.gross_total:  # a free invoice owes nothing (and an entry is never 0)
        return None
    return LedgerEntry.objects.create(
        customer_id=customer_id, kind=LedgerEntry.INVOICE, entry_date=invoice.invoice_date,
        amount=invoice.gross_total, invoice=invoice, description=f'Invoice {invoice.number_display}',
        created_by=invoice.issued_by)


def post_credit_note(note, customer_id):
    if not note.gross_total:
        return None
    return LedgerEntry.objects.create(
        customer_id=customer_id, kind=LedgerEntry.CREDIT_NOTE, entry_date=note.credit_date,
        amount=-note.gross_total, credit_note=note, description=f'Credit note {note.number_display}',
        created_by=note.issued_by)


def post_adjustment(customer, amount, reason, user, client_ref=None):
    """Staff only; ``amount`` a Decimal to the penny, not 0, at most £999,999.99 either way; a reason. Sent again
    with the same ``client_ref`` (a retry after a lost answer), it returns the first entry with ``repeated`` set;
    with the same ref but a different amount or reason it raises ``AdjustmentConflict``."""
    if not getattr(user, 'is_staff', False):
        raise LedgerError('Only staff post adjustments.')
    if not isinstance(amount, Decimal):
        raise LedgerError('The amount must be a decimal number.')
    try:
        if not amount.is_finite() or amount != amount.quantize(Decimal('0.01')):
            raise LedgerError('The amount is in pounds and pence (2 decimal places).')
    except InvalidOperation:
        raise LedgerError('The amount is too large.') from None
    if amount == 0:
        raise LedgerError('An adjustment cannot be 0.')
    if abs(amount) > ADJUSTMENT_LIMIT:
        raise LedgerError(f'An adjustment is at most £{ADJUSTMENT_LIMIT:,} either way.')
    reason = (reason or '').strip()
    if not reason:
        raise LedgerError('Give a reason for the adjustment.')
    if len(reason) > 255:
        raise LedgerError('The reason is at most 255 characters.')
    if client_ref is not None:
        earlier = _repeat(customer, client_ref, amount, reason)
        if earlier is not None:
            return earlier
    try:
        with transaction.atomic():
            entry = LedgerEntry.objects.create(
                customer=customer, kind=LedgerEntry.ADJUSTMENT, entry_date=uk_date(), amount=amount.quantize(ZERO),
                description='Debit adjustment' if amount > 0 else 'Credit adjustment', reason=reason,
                client_ref=client_ref, created_by=user)
    except IntegrityError:
        earlier = client_ref is not None and _repeat(customer, client_ref, amount, reason)
        if not earlier:
            raise
        return earlier  # the same retry, committed meanwhile
    entry.repeated = False
    return entry


def _repeat(customer, client_ref, amount, reason):
    earlier = LedgerEntry.objects.filter(customer=customer, client_ref=client_ref).first()
    if earlier is None:
        return None
    if (earlier.amount, earlier.reason) != (amount, reason):
        raise AdjustmentConflict('This client_ref was used for a different adjustment.')
    earlier.repeated = True
    return earlier


def _sum(entries):
    return (entries.aggregate(total=Sum('amount'))['total'] or ZERO).quantize(ZERO)


def balance(customer, on=None):
    """What the customer owes at the end of ``on`` (default: everything posted)."""
    entries = LedgerEntry.objects.filter(customer=customer)
    if on is not None:
        entries = entries.filter(entry_date__lte=on)
    return _sum(entries)


def statement(customer, start, end):
    """``{opening, entries, closing}``: the balance before ``start``, the entries from ``start`` to ``end`` (UK dates,
    inclusive) each with ``running`` (the balance after it), and the balance after the last of them."""
    opening = _sum(LedgerEntry.objects.filter(customer=customer, entry_date__lt=start))
    entries = list(LedgerEntry.objects.filter(customer=customer, entry_date__gte=start, entry_date__lte=end)
                   .order_by('entry_date', 'id'))
    running = opening
    for entry in entries:
        running += entry.amount
        entry.running = running
    return {'opening': opening, 'entries': entries, 'closing': running}
