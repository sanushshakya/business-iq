"""
Delivery runs (G44): planned -> out -> completed (or planned -> cancelled).

A run takes picked orders (G42) on one of the company's vans, driven by an operator with the driver role, stops in
the order given. The stock an order moves is its picks: each pick's lot has a twin with the same batch number at
DISPATCH (and on the van), so starting moves those amounts DISPATCH -> van and the order becomes dispatched; a
delivery moves them van -> CUSTOMER with proof of delivery and the order becomes delivered (invoiced if its invoice
is already issued, G43); completing brings stops not delivered back van -> DISPATCH and their orders to picked.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from inventory.ledger import LedgerError, dispatch_location, move, relocate, virtual_location
from inventory.models import Location, Product, StockBatch
from tenants.models import Company
from tenants.roles import DRIVER

from .models import CustomerReturn, DeliveryRun, DeliveryStop, Invoice, SalesOrder, SalesOrderPick
from .orders import OrderStateError, advance
from .returns import ReturnError, receive_door_returns, refuse_at_door

R, S = DeliveryRun, SalesOrder
CLOCK_SLACK = timedelta(minutes=5)  # phones' clocks drift


class RunError(ValueError):
    """The run or stop can't make this move; the message is for the API."""


def _is_staff(user):
    return user.is_staff or user.is_superuser


def _check_vehicle_and_driver(company_id, vehicle, driver):
    if vehicle.company_id != company_id or vehicle.type != Location.VAN or not vehicle.is_active:
        raise RunError(f'{vehicle.code} is not one of your active vans.')
    if vehicle.sellable or vehicle.online_sellable:  # tills and the web would sell what is promised to the run
        raise RunError(f'{vehicle.code} is sellable; a delivery van must not be.')
    if driver.company_id != company_id or not driver.is_operator or driver.role != DRIVER or not driver.is_active:
        raise RunError('The driver must be one of your operators with the driver role.')


def _lock_orders(company_id, orders, run=None):
    """Lock the orders (in id order) and check each can go on ``run``: picked, ours, on no other active run."""
    ids = [order.pk for order in orders]
    if len(set(ids)) != len(ids):
        raise RunError('Each order once per run.')
    locked = {order.pk: order for order in S.objects.select_for_update().filter(pk__in=ids).order_by('pk')}
    for order_id in ids:
        order = locked.get(order_id)
        if order is None or order.company_id != company_id:
            raise RunError("Another company's order can't go on this run.")
        if order.status != S.PICKED:
            raise RunError(f'Order {order.number_display} is {order.get_status_display().lower()}, not picked.')
        busy = DeliveryStop.objects.filter(order=order, run__status__in=[R.PLANNED, R.OUT])
        if run is not None:
            busy = busy.exclude(run=run)
        if busy.exists():
            raise RunError(f'Order {order.number_display} is already on another run.')
    return [locked[order_id] for order_id in ids]


def _write_stops(run, orders):
    run.stops.all().delete()
    DeliveryStop.objects.bulk_create(
        DeliveryStop(run=run, order=order, position=position) for position, order in enumerate(orders, start=1))


@transaction.atomic
def create_run(company, user, *, run_date, vehicle, driver, orders, note=''):
    company_id = getattr(company, 'pk', company)
    _check_vehicle_and_driver(company_id, vehicle, driver)
    Company.objects.select_for_update().get(pk=company_id)  # one number at a time per company
    orders = _lock_orders(company_id, orders)
    last = DeliveryRun.objects.filter(company_id=company_id).aggregate(last=Max('number'))['last']
    run = DeliveryRun.objects.create(company_id=company_id, number=(last or 0) + 1, run_date=run_date,
                                     vehicle=vehicle, driver=driver, note=note, created_by=user)
    _write_stops(run, orders)
    return run


def _locked(run, *statuses):
    run = DeliveryRun.objects.select_for_update().get(pk=run.pk)
    if run.status not in statuses:
        raise RunError(f'This run is {run.get_status_display().lower()}.')
    return run


@transaction.atomic
def update_run(run, *, run_date=None, vehicle=None, driver=None, note=None, orders=None):
    """Change a planned run; ``orders`` re-plans the stops in that order."""
    run = _locked(run, R.PLANNED)
    vehicle, driver = vehicle or run.vehicle, driver or run.driver
    _check_vehicle_and_driver(run.company_id, vehicle, driver)
    run.vehicle, run.driver = vehicle, driver
    if run_date is not None:
        run.run_date = run_date
    if note is not None:
        run.note = note
    run.save()
    if orders is not None:
        _write_stops(run, _lock_orders(run.company_id, orders, run=run))
    return run


def set_stops(run, orders):
    return update_run(run, orders=orders)


def _order_lots(order, location, *, with_picks=False):
    """``[(lot at location, units, kg)]`` (with the pick first when ``with_picks``): the order's picks, found at
    ``location`` by batch number. Lock the lots first (``_lock_lots``) where they are changed."""
    moves = []
    picks = SalesOrderPick.objects.filter(line__order=order).select_related('batch', 'line__product').order_by(
        'batch__product_id', 'batch__batch_number', 'pk')
    for pick in picks:
        lot = (StockBatch.objects.select_related('location', 'product')
               .filter(product_id=pick.batch.product_id, batch_number=pick.batch.batch_number, location=location)
               .first())
        if lot is None:
            raise RunError(f'{pick.line.product.name} lot {pick.batch.batch_number} is not at {location.code}.')
        moves.append((pick, lot, pick.quantity, pick.weight_kg) if with_picks else (lot, pick.quantity,
                                                                                   pick.weight_kg))
    return moves


def _lock_lots(orders, source, to=None):
    """Lock what moving these orders touches, products then lots, each in id order (as tills and picking do), so
    a run never waits on a till that waits on it."""
    lots = [lot for order in orders for lot, _quantity, _kg in _order_lots(order, source)]
    product_ids = sorted({lot.product_id for lot in lots})
    list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk').values_list('pk', flat=True))
    twins = StockBatch.objects.none() if to is None else StockBatch.objects.filter(
        location=to, product_id__in=product_ids, batch_number__in={lot.batch_number for lot in lots})
    lot_ids = sorted({lot.pk for lot in lots} | set(twins.values_list('pk', flat=True)))
    list(StockBatch.objects.select_for_update(of=('self',)).filter(pk__in=lot_ids).order_by('pk')
         .values_list('pk', flat=True))


def _relocate_order(order, source, to, *, what_is_there=False):
    """Move the order's picks from ``source`` to ``to``. With ``what_is_there`` (bringing a van back), a lot
    holding less than the picks moves what it holds, and the order then owes what came back: the picks and the
    lines' picked amounts are lowered to it (the loss stays booked where it happened, e.g. a write-off on the van).
    Returns whether anything was lowered."""
    lowered = False
    try:
        for pick, lot, quantity, kg in _order_lots(order, source, with_picks=True):
            if what_is_there:
                lot.refresh_from_db()
                quantity = min(quantity, lot.quantity)
                kg = None if kg is None else min(kg, lot.weight_kg)
                if (quantity, kg) != (pick.quantity, pick.weight_kg):
                    pick.quantity, pick.weight_kg = quantity, kg
                    pick.save(update_fields=['quantity', 'weight_kg'])
                    lowered = True
                if not quantity and not kg:
                    continue
            relocate(lot, quantity=quantity, weight_kg=kg, to=to)
    except LedgerError as error:
        raise RunError(f'Order {order.number_display}: {error}') from None
    if lowered:
        _owe_what_was_picked(order)
    return lowered


def _owe_what_was_picked(order):
    """Set the lines' picked amounts from the (lowered) picks and rebuild a draft invoice."""
    from .invoices import InvoiceError, refresh_draft

    for line in order.lines.select_related('product').exclude(picked_quantity=None, picked_weight_kg=None):
        totals = line.picks.aggregate(units=Sum('quantity'), kg=Sum('weight_kg'))
        loose = line.product.is_weighed and not line.product.is_catch_weight
        line.picked_quantity = None if loose else totals['units'] or 0
        line.picked_weight_kg = (totals['kg'] or Decimal('0.000')) if line.product.is_weighed else None
        line.save(update_fields=['picked_quantity', 'picked_weight_kg'])
    draft = Invoice.objects.filter(order=order, status=Invoice.DRAFT).first()
    if draft is not None:
        try:
            refresh_draft(draft)
        except InvoiceError:  # nothing left to bill: the draft goes
            draft.delete()


def _check_dispatch_covers_every_picked_order(order, dispatch):
    """Orders share a lot at DISPATCH (same batch number). Before one order's run takes its share, the lot must
    still cover every picked order's picks from it, so a shortage there stops each run that needs it instead of
    being passed silently to the order that leaves last."""
    for lot, _quantity, _kg in _order_lots(order, dispatch):
        claims = SalesOrderPick.objects.filter(
            line__order__status=S.PICKED, line__order__company_id=order.company_id,
            batch__product_id=lot.product_id, batch__batch_number=lot.batch_number,
        ).aggregate(units=Sum('quantity'), kg=Sum('weight_kg'))
        units, kg = claims['units'] or 0, claims['kg'] or Decimal('0')
        if lot.quantity < units or (lot.product.is_weighed and lot.weight_kg < kg):
            raise RunError(f'Lot {lot.batch_number} at {dispatch.code} holds less than the picked orders need '
                           f'({lot.quantity} of {units} units, {lot.weight_kg} of {kg} kg); check it first.')


@transaction.atomic
def start_run(run, user):
    run = _locked(run, R.PLANNED)
    _check_vehicle_and_driver(run.company_id, run.vehicle, run.driver)  # still a van, still a driver
    stops = list(run.stops.select_related('order').order_by('order_id'))  # orders locked in id order
    if not stops:
        raise RunError('A run needs at least one stop.')
    dispatch = dispatch_location(run.company_id)
    orders = list(S.objects.select_for_update().filter(pk__in=[stop.order_id for stop in stops]).order_by('pk'))
    _lock_lots(orders, dispatch, run.vehicle)
    for stop in stops:
        order = S.objects.get(pk=stop.order_id)
        if order.status != S.PICKED:
            raise RunError(f'Order {order.number_display} is {order.get_status_display().lower()}, not picked.')
        _check_dispatch_covers_every_picked_order(order, dispatch)
        _relocate_order(order, dispatch, run.vehicle)
        advance(order, S.DISPATCHED)
    run.status, run.started_at = R.OUT, timezone.now()
    run.save(update_fields=['status', 'started_at'])
    return run


def _stop(stop, user):
    run = _locked(stop.run, R.OUT)
    stop = DeliveryStop.objects.select_for_update().get(pk=stop.pk, run=run)
    if stop.status != DeliveryStop.PENDING:
        raise RunError(f'This stop is already {stop.get_status_display().lower()}.')
    if not (_is_staff(user) or (user.pk == run.driver_id and user.company_id == run.company_id)):
        raise RunError("Only staff or this run's driver record its deliveries.")
    return run, stop


@transaction.atomic
def deliver(stop, user, *, received_by, delivered_at=None, notes='', photo=None, refused=None):
    received_by = (received_by or '').strip()
    if not received_by:
        raise RunError('Who received it?')
    run, stop = _stop(stop, user)
    now = timezone.now()
    if delivered_at is not None and not (run.started_at - CLOCK_SLACK <= delivered_at <= now + CLOCK_SLACK):
        raise RunError('The delivery time must be between the start of the run and now.')
    order = S.objects.select_for_update().get(pk=stop.order_id)
    customer = virtual_location(run.company_id, Location.CUSTOMER)
    _lock_lots([order], run.vehicle)
    try:
        # Refused at the door (G45): stays on the van, goes back by reason when the run is completed.
        left_on_van = refuse_at_door(order, stop, user, refused or [])
        for lot, quantity, kg in _order_lots(order, run.vehicle):
            key = (lot.product_id, lot.batch_number)
            keep_units, keep_kg = left_on_van.get(key, (0, Decimal('0')))
            units, kept_units = max(quantity - keep_units, 0), min(quantity, keep_units)
            weight = None if kg is None else max(kg - keep_kg, Decimal('0'))
            kept_kg = Decimal('0') if kg is None else min(kg, keep_kg)
            left_on_van[key] = (keep_units - kept_units, keep_kg - kept_kg)
            if units or weight:
                move(lot, units, 'outbound', counterpart=customer,
                     weight_kg=weight if lot.product.is_weighed else None)
        advance(order, S.DELIVERED)
        if Invoice.objects.filter(order=order, status=Invoice.ISSUED).exists():
            advance(order, S.INVOICED)
        _rebill_draft(order)
    except (LedgerError, OrderStateError, ReturnError) as error:
        raise RunError(str(error)) from None
    stop.status, stop.received_by, stop.notes = DeliveryStop.DELIVERED, received_by[:120], notes or ''
    stop.delivered_at, stop.recorded_by = delivered_at or timezone.now(), user
    if photo is not None:
        stop.photo = photo
    stop.save()
    return stop


def _rebill_draft(order):
    """A draft invoice bills only what reached the customer: rebuilt after the door (dropped if nothing did)."""
    from .invoices import InvoiceError, refresh_draft

    draft = Invoice.objects.filter(order=order, status=Invoice.DRAFT).first()
    if draft is not None:
        try:
            refresh_draft(draft)
        except InvoiceError:
            draft.delete()


@transaction.atomic
def fail_stop(stop, user, reason):
    reason = (reason or '').strip()
    if not reason:
        raise RunError('Say why it was not delivered.')
    _run, stop = _stop(stop, user)
    stop.status, stop.failed_reason, stop.recorded_by = DeliveryStop.FAILED, reason[:255], user
    stop.save(update_fields=['status', 'failed_reason', 'recorded_by'])
    return stop


@transaction.atomic
def complete_run(run, user):
    run = _locked(run, R.OUT)
    stops = list(run.stops.order_by('order_id'))
    if any(stop.status == DeliveryStop.PENDING for stop in stops):
        raise RunError('Record every stop (delivered or not) before completing the run.')
    dispatch = dispatch_location(run.company_id)
    failed = [stop for stop in stops if stop.status == DeliveryStop.FAILED]
    refusing = set(CustomerReturn.objects.filter(stop__run=run, status=CustomerReturn.ON_VAN)
                   .values_list('order_id', flat=True))
    every = list(S.objects.select_for_update().filter(pk__in={stop.order_id for stop in failed} | refusing)
                 .order_by('pk'))
    _lock_lots(every, run.vehicle, dispatch)
    try:
        receive_door_returns(run, run.vehicle)  # refused at the door: off the van, by reason
    except LedgerError as error:
        raise RunError(str(error)) from None
    orders = [order for order in every if order.pk in {stop.order_id for stop in failed}]
    for order in orders:
        _relocate_order(order, run.vehicle, dispatch, what_is_there=True)  # back off the van
        advance(order, S.PICKED)
    run.status, run.completed_at = R.COMPLETED, timezone.now()
    run.save(update_fields=['status', 'completed_at'])
    return run


@transaction.atomic
def cancel_run(run, user):
    run = _locked(run, R.PLANNED)
    run.status = R.CANCELLED
    run.save(update_fields=['status'])
    return run
