"""
Reserving warehouse stock for trade orders (G41). A reservation holds part of a batch for an order line until it
is picked (G42); nothing moves, so no ``StockMovement`` is written. Units for each products, pieces for
catch-weight ones, kg for loose ones. Reserving takes free in-date stock FEFO with the batches locked, so two
orders never reserve more than a batch holds; the other FEFO takes in ``ledger`` skip what is reserved.
"""

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .ledger import _fefo_batches, reserved_by_batch
from .models import StockBatch, StockReservation


def _loose(product):
    return product.is_weighed and not product.is_catch_weight


def _needed(line):
    return line.weight_kg if _loose(line.product) else line.quantity


def reserved_amounts(lines):
    """``{line id: amount reserved}`` (kg for loose products, else units) for order lines."""
    totals = {line.pk: 0 for line in lines}
    rows = (StockReservation.objects.filter(order_line__in=[line.pk for line in lines], released_at__isnull=True)
            .values('order_line_id').annotate(units=Sum('quantity'), kg=Sum('weight_kg')))
    loose = {line.pk: _loose(line.product) for line in lines}
    for row in rows:
        line_id = row['order_line_id']
        totals[line_id] = (row['kg'] or 0) if loose[line_id] else row['units']
    return totals


def reserve(line, location, today=None):
    """
    Top ``line`` up to what it needs from free in-date stock at ``location``, earliest expiry first; returns what is
    still short. Inside ``transaction.atomic()`` (the batches are locked).
    """
    product = line.product
    loose = _loose(product)
    needed = _needed(line) - reserved_amounts([line])[line.pk]
    if needed <= 0:
        return Decimal('0') if loose else 0
    for batch in _fefo_batches(product, [location], today, by='weight_kg' if loose else 'quantity'):
        if needed <= 0:
            break
        take = min(batch.free, needed)
        StockReservation.objects.create(batch=batch, order_line=line, quantity=0 if loose else take,
                                        weight_kg=take if loose else None)
        needed -= take
    return needed


def release(order):
    """Let go of everything ``order`` holds (cancelled, or picked in G42). Inside ``transaction.atomic()``."""
    now = timezone.now()
    for reservation in StockReservation.objects.select_for_update().filter(order_line__order=order,
                                                                          released_at__isnull=True):
        reservation.released_at = now
        reservation.save(update_fields=['released_at'])  # the signal queues the storefront update


def hold_picked(line, picked_by_batch):
    """
    Make a line's reservations follow what it has picked (G42), so stock in a picker's hands can't be reserved by
    another order before the pick is finished: each lot picked from holds at least what was picked there (kg capped
    at the lot's free kg, for loose products), and the line gives up what it then holds beyond what it needs,
    newest first, on lots it hasn't picked as much from. ``picked_by_batch`` is ``{batch id: amount picked}``.
    Inside ``transaction.atomic()``, with the picked lots locked.
    """
    loose = _loose(line.product)
    field = 'weight_kg' if loose else 'quantity'
    rows = list(StockReservation.objects.select_for_update().filter(order_line=line, released_at__isnull=True)
                .order_by('-created_at', '-id'))
    held = {}
    for row in rows:
        held[row.batch_id] = held.get(row.batch_id, 0) + (getattr(row, field) or 0)
    for batch_id, picked in picked_by_batch.items():
        extra = picked - held.get(batch_id, 0)
        if loose and extra > 0:
            on_hand = StockBatch.objects.filter(pk=batch_id).values_list('weight_kg', flat=True).get()
            extra = min(extra, on_hand - reserved_by_batch([batch_id], loose=True).get(batch_id, 0))
        if extra > 0:
            rows.insert(0, StockReservation.objects.create(batch_id=batch_id, order_line=line,
                                                           quantity=0 if loose else extra,
                                                           weight_kg=extra if loose else None))
            held[batch_id] = held.get(batch_id, 0) + extra
    excess = sum(held.values()) - max(_needed(line), sum(picked_by_batch.values()))
    for row in rows:
        if excess <= 0:
            break
        spare = held[row.batch_id] - picked_by_batch.get(row.batch_id, 0)
        cut = min(getattr(row, field) or 0, spare, excess)
        if cut <= 0:
            continue
        setattr(row, field, getattr(row, field) - cut)
        if not getattr(row, field):
            row.released_at = timezone.now()
        row.save(update_fields=[field, 'released_at'])
        held[row.batch_id] -= cut
        excess -= cut
