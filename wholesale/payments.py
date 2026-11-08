"""
Payments and allocation (G49). A payment posts a ``payment`` entry to the customer's ledger; allocations say which
debits (invoices, debit adjustments) its credit pays. Credits are payments, credit notes and credit adjustments.
The balance is always the sum of the ledger: allocating never changes it, it only decides what is open (the credit
check, statements and aged debtors read that). Every change locks the customer row, so two at once never
over-allocate. Staff only.
"""

from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import DecimalField, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from vat.services import uk_date

from .models import Allocation, LedgerEntry, Payment, TradeCustomer

ZERO = Decimal('0.00')
PENNY = Decimal('0.01')
MAX_PAYMENT = Decimal('9999999.99')
METHODS = {value for value, _label in Payment.METHODS}
DEBIT_KINDS = [LedgerEntry.INVOICE, LedgerEntry.ADJUSTMENT]
CREDIT_KINDS = [LedgerEntry.PAYMENT, LedgerEntry.CREDIT_NOTE, LedgerEntry.ADJUSTMENT]
MONEY = DecimalField(max_digits=14, decimal_places=2)


class PaymentError(ValueError):
    """Refused; ``detail`` is for the API."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def _active_sum(side):
    rows = (Allocation.objects.filter(**{side: OuterRef('pk')}, released_at__isnull=True).order_by()
            .values(side).annotate(total=Sum('amount')).values('total'))
    return Coalesce(Subquery(rows, output_field=MONEY), Value(ZERO), output_field=MONEY)


def open_debits(customer):
    """Invoices and debit adjustments with something still open (``open``), oldest first."""
    return (LedgerEntry.objects.filter(customer=customer, amount__gt=0, kind__in=DEBIT_KINDS)
            .annotate(open=F('amount') - _active_sum('debit')).filter(open__gt=0)
            .select_related('invoice').order_by('entry_date', 'id'))


def on_account(customer):
    """Credits not (fully) allocated, with what is left (``remaining``), oldest first."""
    return (LedgerEntry.objects.filter(customer=customer, amount__lt=0, kind__in=CREDIT_KINDS)
            .annotate(remaining=-F('amount') - _active_sum('credit')).filter(remaining__gt=0)
            .order_by('entry_date', 'id'))


class PaymentConflict(PaymentError):
    """The ``client_ref`` was already used for a different payment."""


def _staff(user):
    if not getattr(user, 'is_staff', False):
        raise PaymentError('Only staff record and allocate payments.')


def _lock(customer):
    return TradeCustomer.objects.select_for_update().get(pk=customer.pk)


def _money(value, name, limit):
    if not isinstance(value, Decimal):
        raise PaymentError(f'The {name} must be a decimal number.')
    try:
        if not value.is_finite() or value != value.quantize(PENNY):
            raise PaymentError(f'The {name} is in pounds and pence (2 decimal places).')
    except InvalidOperation:
        raise PaymentError(f'The {name} is too large.') from None
    if value <= 0:
        raise PaymentError(f'The {name} must be above 0.')
    if value > limit:
        raise PaymentError(f'The {name} is at most £{limit:,}.')
    return value


def _allocate(credit, user, targets):
    """Under the customer lock: ``targets`` [(debit entry id, amount)] checked against what is open now."""
    remaining = on_account(credit.customer).filter(pk=credit.pk).values_list('remaining', flat=True).first() or ZERO
    ids = [debit_id for debit_id, _amount in targets]
    if len(set(ids)) != len(ids):
        raise PaymentError('Each invoice once per allocation.')
    open_now = dict(open_debits(credit.customer).filter(pk__in=ids).values_list('pk', 'open'))
    total = ZERO
    rows = []
    for debit_id, amount in targets:
        _money(amount, 'amount', MAX_PAYMENT)
        if debit_id not in open_now:
            raise PaymentError(f'Entry {debit_id} is not an open invoice or charge of this customer.')
        if amount > open_now[debit_id]:
            raise PaymentError(f'Only £{open_now[debit_id]} is open on entry {debit_id}.')
        total += amount
        rows.append(Allocation(credit=credit, debit_id=debit_id, amount=amount, created_by=user))
    if total > remaining:
        raise PaymentError(f'That allocates £{total}; only £{remaining} is left to allocate.')
    return Allocation.objects.bulk_create(rows)


def _allocate_oldest(credit, user):
    remaining = on_account(credit.customer).filter(pk=credit.pk).values_list('remaining', flat=True).first() or ZERO
    targets = []
    for debit in open_debits(credit.customer):
        if remaining <= 0:
            break
        take = min(remaining, debit.open)
        targets.append((debit.pk, take))
        remaining -= take
    return _allocate(credit, user, targets) if targets else []


def _targets(given):
    try:
        return [(int(row['debit']), row['amount']) for row in given]
    except (KeyError, TypeError, ValueError):
        raise PaymentError('Allocations are [{debit, amount}].') from None


@transaction.atomic
def allocate(credit, user, targets='oldest'):
    """Allocate an on-account credit: ``'oldest'`` or ``[{debit, amount}]``."""
    _staff(user)
    customer = _lock(credit.customer)
    credit = LedgerEntry.objects.get(pk=credit.pk, customer=customer)
    if credit.amount >= 0 or credit.kind not in CREDIT_KINDS:
        raise PaymentError('Only a payment, credit note or credit adjustment is allocated.')
    return _allocate_oldest(credit, user) if targets == 'oldest' else _allocate(credit, user, _targets(targets))


@transaction.atomic
def release(allocation, user):
    _staff(user)
    customer = _lock(allocation.debit.customer)
    allocation = Allocation.objects.select_for_update().get(pk=allocation.pk, debit__customer=customer)
    if allocation.released_at is not None:
        raise PaymentError('This allocation is already released.')
    if allocation.credit.kind == LedgerEntry.PAYMENT and allocation.credit.payment.reversed_at is not None:
        raise PaymentError('A reversed payment stays matched to its reversal.')
    allocation.released_at, allocation.released_by = timezone.now(), user
    allocation.save(update_fields=['released_at', 'released_by'])
    return allocation


def _repeat(customer, client_ref, amount, method):
    earlier = Payment.objects.filter(customer=customer, client_ref=client_ref).first()
    if earlier is None:
        return None
    if (earlier.amount, earlier.method) != (amount, method):
        raise PaymentConflict('This client_ref was used for a different payment.')
    earlier.repeated = True
    return earlier


@transaction.atomic
def record_payment(customer, user, *, amount, method, received_on, reference='', note='', allocate='oldest',
                   client_ref=None):
    """Record a payment and post it; then allocate ``'oldest'``, as ``[{debit, amount}]``, or not (``None``: all
    on account). The same ``client_ref`` again returns the first payment (``repeated``)."""
    _staff(user)
    _money(amount, 'amount', MAX_PAYMENT)
    if method not in METHODS:
        raise PaymentError(f'The method is one of {", ".join(sorted(METHODS))}.')
    if received_on > uk_date():
        raise PaymentError('A payment is not received in the future.')
    reference, note = (reference or '').strip(), (note or '').strip()
    if len(reference) > 60 or len(note) > 255:
        raise PaymentError('The reference is at most 60 characters and the note 255.')
    customer = _lock(customer)
    if client_ref is not None:
        earlier = _repeat(customer, client_ref, amount, method)
        if earlier is not None:
            return earlier
    try:
        with transaction.atomic():
            payment = Payment.objects.create(customer=customer, method=method, amount=amount, received_on=received_on,
                                             reference=reference, note=note, client_ref=client_ref, created_by=user)
    except IntegrityError:
        raise PaymentError('This client_ref was used at the same time; send it again.') from None
    label = payment.get_method_display()
    entry = LedgerEntry.objects.create(
        customer=customer, kind=LedgerEntry.PAYMENT, entry_date=received_on, amount=-amount, payment=payment,
        payment_ref=reference, description=f'Payment ({label}){f" {reference}" if reference else ""}'[:120],
        created_by=user)
    if allocate == 'oldest':
        _allocate_oldest(entry, user)
    elif allocate is not None:
        _allocate(entry, user, _targets(allocate))
    payment.repeated = False
    return payment


@transaction.atomic
def reverse_payment(payment, user, reason):
    """Cancel a payment (keyed wrongly, a bounced cheque): its allocations are released and a ``payment_reversal``
    entry (+amount, today) is matched to it, so neither is open or on account."""
    _staff(user)
    reason = (reason or '').strip()
    if not reason or len(reason) > 255:
        raise PaymentError('Give a reason (at most 255 characters).')
    customer = _lock(payment.customer)
    payment = Payment.objects.select_for_update().get(pk=payment.pk, customer=customer)
    if payment.reversed_at is not None:
        raise PaymentError('This payment is already reversed.')
    entry = payment.ledger_entries.get(kind=LedgerEntry.PAYMENT)
    now = timezone.now()
    for allocation in Allocation.objects.select_for_update().filter(credit=entry, released_at__isnull=True):
        allocation.released_at, allocation.released_by = now, user
        allocation.save(update_fields=['released_at', 'released_by'])
    reversal = LedgerEntry.objects.create(
        customer=customer, kind=LedgerEntry.PAYMENT_REVERSAL, entry_date=uk_date(), amount=payment.amount,
        payment=payment, payment_ref=payment.reference, reason=reason,
        description=f'Payment reversed{f" {payment.reference}" if payment.reference else ""}'[:120], created_by=user)
    Allocation.objects.create(credit=entry, debit=reversal, amount=payment.amount, created_by=user)
    payment.reversed_at, payment.reversed_by, payment.reversal_reason = now, user, reason
    payment.save(update_fields=['reversed_at', 'reversed_by', 'reversal_reason'])
    return payment


def allocate_credit_note(entry, user):
    """Inside ``issue_credit_note``: the note pays its own invoice, up to what is open there; the rest is on
    account. Locks the customer like every other allocation."""
    if entry is None:
        return
    _lock(entry.customer)
    invoice_entry = LedgerEntry.objects.filter(invoice_id=entry.credit_note.invoice_id).first()
    if invoice_entry is None:
        return
    open_now = open_debits(entry.customer).filter(pk=invoice_entry.pk).values_list('open', flat=True).first()
    if open_now:
        _allocate(entry, user, [(invoice_entry.pk, min(open_now, -entry.amount))])
