# inventory/write_offs.py

"""
Waste write-offs (G23): stock from one batch goes out to the company's WASTE location, with a reason.

The cost is the amount × the batch's landed ``unit_cost`` (kg for weighed products), to the penny. A write-off whose
cost is known and at most the company's threshold (``Setting`` ``write_off_approval_threshold``, default £50) moves at
once; anything above it, or with no known cost, waits ``pending`` until a staff member other than the one who raised
it approves (the stock moves then, after checking the batch still holds it) or rejects it (nothing moves). The
amount, kg and cost the approver sees are the ones raised: a catch-weight batch whose pieces' kg has changed since
(other pieces sold) is refused at approval, and a later change to the batch's cost does not change the write-off. Every
movement carries ``write_off``; stock changes only through ``ledger.move``.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from common.services.settings_service import get_company_setting

from .ledger import KG, move, virtual_location
from .models import Location, StockBatch, WriteOff
from .transfers import next_number

THRESHOLD_KEY, DEFAULT_THRESHOLD = 'write_off_approval_threshold', Decimal('50.00')
CENTS = Decimal('0.01')
MAX_COST = Decimal('10000000000')  # WriteOff.cost holds 12 digits, 2 after the point


class WriteOffError(Exception):
    """A write-off that cannot be raised or decided; ``detail`` maps fields to messages (a DRF-style 400 body)."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def approval_threshold(company_id):
    """The company's threshold in £; a value that is not a number of 0 or more falls back to £50."""
    threshold = get_company_setting(company_id, THRESHOLD_KEY, DEFAULT_THRESHOLD, Decimal)
    if not threshold.is_finite() or threshold < 0:  # a typo must not stop write-offs or wave them all through
        return DEFAULT_THRESHOLD
    return threshold.quantize(CENTS, ROUND_HALF_UP)


def raise_write_off(*, company, user, batch, reason, quantity=0, weight_kg=None, note=''):
    """
    Raise a write-off of ``quantity`` units (each products; pieces for catch-weight, whose kg is the batch's average
    per piece) or ``weight_kg`` (loose products) from ``batch``. Returns it, written off or pending.
    """
    if reason not in dict(WriteOff.REASONS):
        raise WriteOffError({'reason': [f'Choose a reason: {", ".join(dict(WriteOff.REASONS))}.']})
    with transaction.atomic():
        batch = StockBatch.objects.select_for_update(of=('self',)).select_related('location', 'product').get(
            pk=batch.pk)
        if batch.product.company_id != company.pk:  # the API scopes this; superusers are not scoped
            raise WriteOffError({'batch': ['Invalid pk - object does not exist.']})
        if batch.location.is_virtual:
            raise WriteOffError({'batch': [f'Batch {batch.batch_number} is not on a real location.']})
        quantity, weight_kg = _amount(batch, quantity or 0, weight_kg)
        cost = _cost(batch, quantity, weight_kg)
        if cost is not None and cost >= MAX_COST:
            raise WriteOffError({'batch': [
                f'Batch {batch.batch_number} costs too much to record (check its cost per kg).']})
        write_off = WriteOff.objects.create(
            company=company, number=next_number(WriteOff, company), batch=batch, location=batch.location,
            reason=reason, quantity=quantity, weight_kg=weight_kg, cost=cost, note=(note or '').strip(),
            created_by=user,
        )
        if cost is not None and cost <= approval_threshold(company.pk):
            _book(write_off, batch)
    return write_off


def approve(write_off, *, user, note=''):
    """A staff member other than the raiser approves a pending write-off; the stock goes to WASTE now."""
    with transaction.atomic():
        write_off = _decidable(write_off, user)
        batch = StockBatch.objects.select_for_update(of=('self',)).select_related('location', 'product').get(
            pk=write_off.batch_id)
        held = batch.weight_kg if batch.product.is_weighed and not batch.product.is_catch_weight else batch.quantity
        wanted = write_off.weight_kg if write_off.quantity == 0 else write_off.quantity
        if held < wanted:
            unit = ' kg' if write_off.quantity == 0 else ''
            raise WriteOffError({'detail': [f'Batch {batch.batch_number} now holds {held}{unit}; raise a new '
                                            'write-off.']})
        if batch.product.is_catch_weight:  # the pieces' kg is fixed when raised; the batch may have changed since
            kg = _amount(batch, write_off.quantity, None)[1]
            if kg != write_off.weight_kg:
                raise WriteOffError({'detail': [
                    f'Batch {batch.batch_number} has changed since this was raised ({write_off.quantity} piece(s) '
                    f'is now {kg} kg, not {write_off.weight_kg} kg); reject it and raise a new write-off.']})
        _decide(write_off, user, note, WriteOff.WRITTEN_OFF)
        _book(write_off, batch)
    return write_off


def reject(write_off, *, user, note=''):
    """A staff member other than the raiser rejects a pending write-off; nothing moves."""
    with transaction.atomic():
        write_off = _decidable(write_off, user)
        _decide(write_off, user, note, WriteOff.REJECTED)
        write_off.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
    return write_off


def _amount(batch, quantity, weight_kg):
    """``(quantity, weight_kg)`` to take from the (locked) batch, checked against what it holds."""
    product = batch.product
    if not product.is_weighed:
        if weight_kg is not None:
            raise WriteOffError({'weight_kg': [f'{product.name} is sold by each; leave the kg out.']})
        if quantity < 1:
            raise WriteOffError({'quantity': ['Say how many to write off.']})
        if quantity > batch.quantity:
            raise WriteOffError({'quantity': [f'Batch {batch.batch_number} holds {batch.quantity}.']})
        return quantity, None
    if product.is_catch_weight:
        if weight_kg is not None:
            raise WriteOffError({'weight_kg': ['Send the pieces; their kg comes from the batch.']})
        if quantity < 1:
            raise WriteOffError({'quantity': ['Say how many pieces to write off.']})
        if quantity > batch.quantity:
            raise WriteOffError({'quantity': [f'Batch {batch.batch_number} holds {batch.quantity} piece(s).']})
        if quantity == batch.quantity:  # the last pieces take all the kg left
            return quantity, batch.weight_kg
        return quantity, (batch.weight_kg * quantity / batch.quantity).quantize(KG, rounding=ROUND_HALF_UP)
    if quantity:
        raise WriteOffError({'quantity': [f'{product.name} is sold loose; send the kg.']})
    if weight_kg is None or weight_kg <= 0 or weight_kg != weight_kg.quantize(KG):
        raise WriteOffError({'weight_kg': ['Say how many kg to write off (up to 3 decimals).']})
    if weight_kg > batch.weight_kg:
        raise WriteOffError({'weight_kg': [f'Batch {batch.batch_number} holds {batch.weight_kg} kg.']})
    return 0, weight_kg


def _cost(batch, quantity, weight_kg):
    if batch.unit_cost is None:
        return None
    amount = weight_kg if batch.product.is_weighed else quantity
    return (amount * batch.unit_cost).quantize(CENTS, ROUND_HALF_UP)


def _decidable(write_off, user):
    if not (user.is_staff or user.is_superuser):
        raise WriteOffError({'detail': ['Only staff can approve or reject write-offs.']})
    write_off = WriteOff.objects.select_for_update().get(pk=write_off.pk)
    if write_off.status != WriteOff.PENDING:
        raise WriteOffError({'detail': [f'{write_off.reference} is already {write_off.get_status_display().lower()}.']})
    if write_off.created_by_id == user.pk:
        raise WriteOffError({'detail': ['Someone other than who raised it must approve it.']})
    return write_off


def _decide(write_off, user, note, status):
    write_off.status, write_off.decided_by, write_off.decided_at = status, user, timezone.now()
    write_off.decision_note = (note or '').strip()


def _book(write_off, batch):
    """Move the write-off's stock out of the (locked) batch to WASTE and mark it written off."""
    move(batch, write_off.quantity, 'outbound', counterpart=virtual_location(write_off.company_id, Location.WASTE),
         weight_kg=write_off.weight_kg if batch.product.is_weighed else None, write_off=write_off)
    write_off.status, write_off.written_off_at = WriteOff.WRITTEN_OFF, timezone.now()
    write_off.save()
