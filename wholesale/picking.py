"""
Picking a trade order (G42): confirmed -> picking -> picked.

Pickers record what they take per line and lot (``SalesOrderPick``): a scanned batch number at the order's
warehouse, or a scale/GS1 label (which gives the product and the kg). Units and pieces never go over what was
ordered; kg may go up to 20% over the ordered (or estimated) kg, since the real weight is what is billed (G43). A swap
for another product is proposed on a line and decided by a staff user other than the proposer; an approved swap is
priced like a confirmed line. Finishing releases the order's reservations and moves every pick into the same lot at
DISPATCH (``ledger.pick_batch``, gaps against recorded kg via ADJUSTMENT), then records what each line got.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from inventory.ledger import LedgerError, dispatch_location, pick_batch, reserved_by_batch
from inventory.models import StockBatch, StockReservation
from inventory.reservations import hold_picked, release
from sales.labels import read_label

from .models import KG, SalesOrder, SalesOrderLine, SalesOrderPick, TradeCustomer
from .orders import OrderStateError, advance, price_lines, set_estimates

S = SalesOrder
KG_TOLERANCE = Decimal('1.20')  # picked kg may be up to 20% over what was ordered


def _loose(product):
    return product.is_weighed and not product.is_catch_weight


def _picking(order):
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != S.PICKING:
        raise OrderStateError(f'A {order.get_status_display().lower()} order is not being picked.')
    return order


def _line(order, line_id):
    line = order.lines.select_related('product').filter(pk=line_id).first()
    if line is None:
        raise OrderStateError('Not a line of this order.')
    return line


def start_pick(order):
    from . import credit  # credit imports orders, which this module imports

    credit.check(order)  # G48: the limit, overdue invoices and the manual hold stop picking too
    return advance(order, S.PICKING)


def _amounts(product, quantity, weight_kg, *, need_kg=True):
    """Check the amounts a kind of product is picked (or swapped) in: units, kg (loose), or pieces and kg (a swap
    of a catch-weight product is pieces only: its kg is estimated from the nominal weight)."""
    if weight_kg is not None and (weight_kg <= 0 or weight_kg != weight_kg.quantize(KG)):
        raise OrderStateError('Kg above 0, to 3 decimal places.')
    if quantity is not None and quantity < 1:
        raise OrderStateError('At least 1.')
    if _loose(product):
        if weight_kg is None or quantity:
            raise OrderStateError(f'{product.name} is sold loose by weight: give the kg only.')
        return 0, weight_kg
    if quantity is None:
        raise OrderStateError(f'Give how many {"pieces" if product.is_weighed else "units"} of {product.name}.')
    if product.is_catch_weight and need_kg and weight_kg is None:
        raise OrderStateError(f'{product.name} is weighed per piece: give the kg too.')
    if not product.is_weighed and weight_kg is not None:
        raise OrderStateError(f'{product.name} is sold by each: no kg.')
    return quantity, weight_kg


def _batch_for_label(order, line, today):
    """The lot to take a labelled item from: this line's reserved lots first, then free stock, earliest expiry."""
    reserved = set(StockReservation.objects.filter(order_line=line, released_at__isnull=True)
                   .values_list('batch_id', flat=True))
    held = 'weight_kg' if _loose(line.product) else 'quantity'
    batches = list(StockBatch.objects.filter(product=line.product, location_id=order.source_location_id,
                                             expiration_date__gte=today, **{f'{held}__gt': 0})
                   .order_by('expiration_date', 'receive_date', 'pk'))
    batches.sort(key=lambda batch: batch.pk not in reserved)  # stable: FEFO within each group
    for batch in batches:
        if _available(order, line, batch) > 0:
            return batch
    raise OrderStateError(f'No {line.product.name} left to pick at the warehouse.')


def _available(order, line, batch):
    """What this line may still take from ``batch``: its own reservation there plus free stock, less what this
    order's picks already take from it."""
    loose = _loose(line.product)
    held = batch.weight_kg if loose else batch.quantity
    others = reserved_by_batch([batch.pk], loose=loose).get(batch.pk, 0) - (
        StockReservation.objects.filter(batch=batch, order_line__order=order, released_at__isnull=True)
        .aggregate(total=Sum('weight_kg' if loose else 'quantity'))['total'] or 0)
    picked = SalesOrderPick.objects.filter(line__order=order, batch=batch).aggregate(
        total=Sum('weight_kg' if loose else 'quantity'))['total'] or 0
    return held - others - picked


def _others_hold(order, batch):
    return StockReservation.objects.filter(batch=batch, released_at__isnull=True).exclude(
        order_line__order=order).exists()


@transaction.atomic
def record_pick(order, line_id, *, user, batch_number=None, barcode=None, quantity=None, weight_kg=None):
    order = _picking(order)
    line = _line(order, line_id)
    if line.substitution == SalesOrderLine.PROPOSED:
        raise OrderStateError('This swap is waiting for approval.')
    if line.substitution == SalesOrderLine.REJECTED:
        raise OrderStateError('This swap was rejected; nothing is picked on it.')
    product, today = line.product, timezone.localdate()
    if barcode:
        try:
            label = read_label(order.company, barcode, today)
        except serializers.ValidationError as error:
            raise OrderStateError(' '.join(str(item) for item in error.detail)) from None
        if label.product.pk != product.pk:
            raise OrderStateError(f'That label is {label.product.name}, not {product.name}.')
        quantity = None if _loose(product) else 1  # a label is one item
        weight_kg = label.weight_kg if product.is_weighed else None
    quantity, weight_kg = _amounts(product, quantity, weight_kg)
    if batch_number:
        batch = StockBatch.objects.filter(product=product, location_id=order.source_location_id,
                                          batch_number=batch_number).first()
        if batch is None:
            raise OrderStateError(f'No {product.name} lot {batch_number} at the warehouse.')
        if batch.expiration_date < today:
            raise OrderStateError(f'Lot {batch_number} is out of date.')
    elif barcode:
        batch = _batch_for_label(order, line, today)
    else:
        raise OrderStateError('Scan the lot or a label.')
    batch = StockBatch.objects.select_for_update().get(pk=batch.pk)  # picks of one lot take turns

    picked = line.picks.aggregate(units=Sum('quantity'), kg=Sum('weight_kg'))
    if not _loose(product):
        if (picked['units'] or 0) + quantity > line.quantity:
            raise OrderStateError(f'{line.quantity} ordered; that would pick {(picked["units"] or 0) + quantity}.')
        if quantity > _available(order, line, batch):
            raise OrderStateError(f'Lot {batch.batch_number} has only {max(_available(order, line, batch), 0)} '
                                  'free for this order.')
    else:
        # Real kg may be over the recorded kg (up to 20% of what is there), but only where nobody else holds part
        # of the lot: then the extra would be theirs.
        available = _available(order, line, batch)
        limit = available if _others_hold(order, batch) else (available * KG_TOLERANCE).quantize(KG)
        if available <= 0 or weight_kg > limit:
            raise OrderStateError(f'Lot {batch.batch_number} has {max(available, 0)} kg for this order; '
                                  'check the lot or the weight.')
    if product.is_weighed:
        ordered_kg = line.weight_kg
        if ordered_kg is None:
            raise OrderStateError(f'{product.name} has no ordered kg; confirm the order again.')
        if (picked['kg'] or 0) + weight_kg > (ordered_kg * KG_TOLERANCE).quantize(KG):
            raise OrderStateError(f'{ordered_kg} kg ordered; more than 20% over is not picked.')
    pick = SalesOrderPick.objects.create(line=line, batch=batch, quantity=quantity, weight_kg=weight_kg,
                                         barcode=(barcode or '')[:64], picked_by=user)
    field = 'weight_kg' if _loose(product) else 'quantity'
    picked_by_batch = dict(line.picks.values('batch_id').annotate(total=Sum(field)).values_list('batch_id', 'total'))
    hold_picked(line, picked_by_batch)  # what is in the picker's hands stays this order's until the finish
    return pick


@transaction.atomic
def remove_pick(order, pick_id):
    order = _picking(order)
    deleted, _ = SalesOrderPick.objects.filter(pk=pick_id, line__order=order).delete()
    if not deleted:
        raise OrderStateError('Not a pick of this order.')


@transaction.atomic
def propose_substitute(order, line_id, product, *, user, quantity=None, weight_kg=None):
    order = _picking(order)
    line = _line(order, line_id)
    if line.substitute_for_id is not None:
        raise OrderStateError('Propose a swap for the ordered line, not for a swap.')
    if product.company_id != order.company_id:
        raise OrderStateError("Another company's product can't be sent.")
    if product.pk == line.product_id:
        raise OrderStateError('That is the product ordered.')
    if line.substitutes.filter(substitution=SalesOrderLine.PROPOSED).exists():
        raise OrderStateError('A swap for this line is already waiting for approval.')
    quantity, weight_kg = _amounts(product, quantity, weight_kg if _loose(product) else None, need_kg=False)
    return SalesOrderLine.objects.create(
        order=order, product=product, quantity=quantity if not _loose(product) else None,
        weight_kg=weight_kg if _loose(product) else None, substitute_for=line,
        substitution=SalesOrderLine.PROPOSED, proposed_by=user,
    )


@transaction.atomic
def decide(order, substitute_id, *, approve, user):
    order = _picking(order)
    line = _line(order, substitute_id)
    if line.substitution != SalesOrderLine.PROPOSED:
        raise OrderStateError('Only a proposed swap is approved or rejected.')
    if not (user.is_staff or user.is_superuser):
        raise OrderStateError('Staff approve swaps.')
    if user.pk == line.proposed_by_id:
        raise OrderStateError('Someone other than the proposer decides a swap.')
    if approve:
        customer = TradeCustomer.objects.get(pk=order.customer_id)
        price_lines(order, customer, [line])
    line.substitution = SalesOrderLine.APPROVED if approve else SalesOrderLine.REJECTED
    line.decided_by, line.decided_at = user, timezone.now()
    line.save(update_fields=['substitution', 'decided_by', 'decided_at'])
    if approve:
        set_estimates(order)
        order.save(update_fields=['estimated_net', 'estimated_vat', 'estimated_gross', 'updated_at'])
    return line


@transaction.atomic
def finish_pick(order, user):
    order = _picking(order)
    lines = list(order.lines.select_related('product'))
    if any(line.substitution == SalesOrderLine.PROPOSED for line in lines):
        raise OrderStateError('A swap is waiting for approval.')
    # Products, then lots, in id order, as tills and confirm lock them, so nothing waits on us in a circle.
    picks = list(SalesOrderPick.objects.filter(line__order=order).order_by('batch__product_id', 'batch_id', 'pk'))
    if not picks:
        raise OrderStateError('Nothing has been picked.')
    release(order)
    dispatch = dispatch_location(order.company_id)
    for pick in picks:  # lots locked in id order
        batch = StockBatch.objects.select_for_update(of=('self',)).select_related('location').get(pk=pick.batch_id)
        try:
            pick_batch(batch, quantity=pick.quantity, weight_kg=pick.weight_kg, to=dispatch)
        except LedgerError as error:
            raise OrderStateError(f'{error} Remove that pick, or pick it again from another lot.') from None
    for line in lines:
        if line.substitution == SalesOrderLine.REJECTED:
            continue
        totals = line.picks.aggregate(units=Sum('quantity'), kg=Sum('weight_kg'))
        line.picked_quantity = None if _loose(line.product) else totals['units'] or 0
        line.picked_weight_kg = (totals['kg'] or Decimal('0.000')) if line.product.is_weighed else None
        line.save(update_fields=['picked_quantity', 'picked_weight_kg'])
    order.status = S.PICKED
    order.save(update_fields=['status', 'updated_at'])
    return order
