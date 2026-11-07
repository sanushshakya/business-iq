"""
Returns (G45): goods refused at the door during a delivery, or returned later.

Amounts are units (each), pieces and kg (catch-weight) or kg (loose). They are spread over the lots the order was
picked from (by batch number, in pick order), one ``ReturnLine`` per lot, so stock goes back into the same lots. By
reason: damaged or out of date go to WASTE; wrong item or not wanted back to a location (DISPATCH for door refusals,
the order's warehouse for later returns, or one chosen). A line never gives back more than was delivered: picked,
less refused at the door, less returned before.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from inventory.ledger import (
    LedgerError,
    dispatch_location,
    move,
    relocate,
    take_back,
    virtual_location,
)
from inventory.models import Location, Product, StockBatch
from tenants.models import Company

from .models import KG, CustomerReturn, ReturnLine, SalesOrder, SalesOrderPick

REASONS = {value for value, _label in ReturnLine.REASONS}


class ReturnError(ValueError):
    """The return can't be recorded as asked; the message is for the API."""


def _loose(product):
    return product.is_weighed and not product.is_catch_weight


def _number(company_id):
    Company.objects.select_for_update().get(pk=company_id)
    last = CustomerReturn.objects.filter(company_id=company_id).aggregate(last=Max('number'))['last']
    return (last or 0) + 1


def _lots(line):
    """``[(batch number, template batch, units picked, kg picked)]`` for a line, in pick order, one per lot."""
    lots = {}
    for pick in SalesOrderPick.objects.filter(line=line).select_related('batch').order_by('pk'):
        number = pick.batch.batch_number
        units, kg = lots.get(number, (pick.batch, 0, Decimal('0')))[1:]
        lots[number] = (pick.batch, units + pick.quantity, kg + (pick.weight_kg or 0))
    return [(number, template, units, kg) for number, (template, units, kg) in lots.items()]


def _given_back(line, batch_number=None, kind=None):
    rows = ReturnLine.objects.filter(order_line=line)
    if batch_number is not None:
        rows = rows.filter(batch_number=batch_number)
    if kind is not None:
        rows = rows.filter(customer_return__kind=kind)
    totals = rows.aggregate(units=Sum('quantity'), kg=Sum('weight_kg'))
    return totals['units'] or 0, totals['kg'] or Decimal('0')


def door_refused(line):
    """``(units, kg)`` refused at the door on ``line``: what an invoice must not bill."""
    return _given_back(line, kind=CustomerReturn.DOOR)


def _amounts(line, quantity, weight_kg):
    product = line.product
    if weight_kg is not None and (weight_kg <= 0 or weight_kg != weight_kg.quantize(KG)):
        raise ReturnError('Kg above 0, to 3 decimal places.')
    if _loose(product):
        if weight_kg is None or quantity:
            raise ReturnError(f'{product.name} is sold loose: give the kg only.')
        return 0, weight_kg
    if quantity is None or quantity < 1:
        raise ReturnError(f'Give how many {"pieces" if product.is_weighed else "units"} of {product.name}.')
    if product.is_catch_weight and weight_kg is None:
        raise ReturnError(f'{product.name} is weighed per piece: give the kg too.')
    if not product.is_weighed and weight_kg is not None:
        raise ReturnError(f'{product.name} is sold by each: no kg.')
    return quantity, weight_kg if product.is_weighed else None


def _split(line, quantity, weight_kg):
    """Spread an amount over the line's lots, against what each still has to give back; ``ReturnError`` if it
    can't. Returns ``[(template, units, kg)]``."""
    product = line.product
    lots = []
    for number, template, units, kg in _lots(line):
        back_units, back_kg = _given_back(line, number)
        lots.append((template, units - back_units, kg - back_kg))
    left_units = sum(units for _t, units, _k in lots)
    left_kg = sum((kg for _t, _u, kg in lots), Decimal('0'))
    loose = _loose(product)
    if (not loose and quantity > left_units) or (product.is_weighed and weight_kg > left_kg):
        have = f'{left_kg} kg' if loose else f'{left_units}' + (f' ({left_kg} kg)' if product.is_weighed else '')
        raise ReturnError(f'{product.name}: only {have} delivered and not yet given back.')
    if product.is_catch_weight:  # pieces and kg must stay together
        if (quantity == left_units) != (weight_kg == left_kg):
            raise ReturnError(f'{product.name}: all the pieces go back with all their kg, and only then.')
    parts, units_to_go, kg_to_go = [], quantity, weight_kg or Decimal('0')
    for index, (template, units, kg) in enumerate(lots):
        if loose:
            take_kg = min(kg, kg_to_go)
            if take_kg > 0:
                parts.append((template, 0, take_kg))
            kg_to_go -= take_kg
            continue
        take = min(units, units_to_go)
        if take <= 0:
            continue
        take_kg = None
        if product.is_catch_weight:
            last = take == units_to_go
            take_kg = kg_to_go if last else min(kg, (weight_kg * take / quantity).quantize(KG, ROUND_HALF_UP))
            if take_kg > kg or (take == units and take_kg != kg and not last):
                raise ReturnError(f'{product.name}: give the pieces and kg back lot by lot ({template.batch_number}).')
            kg_to_go -= take_kg
        parts.append((template, take, take_kg))
        units_to_go -= take
    return parts


def _disposition(reason, location, default):
    if reason not in REASONS:
        raise ReturnError(f'Reason: one of {", ".join(sorted(REASONS))}.')
    if reason in ReturnLine.WASTE_REASONS:
        return ReturnLine.WASTE, None
    return ReturnLine.RESTOCK, location or default


def _lines(order, requested, default_location):
    """Check the requested lines; returns ``[(ReturnLine, template)]`` (unsaved)."""
    rows = []
    lines = {line.pk: line for line in order.lines.select_related('product')}
    for item in requested:
        line = lines.get(item.get('line'))
        if line is None:
            raise ReturnError('Not a line of this order.')
        quantity, weight_kg = _amounts(line, item.get('quantity'), item.get('weight_kg'))
        disposition, location = _disposition(item.get('reason'), item.get('location'), default_location)
        if location is not None and (location.company_id != order.company_id or location.is_virtual
                                     or not location.is_active):
            raise ReturnError('Put it back at one of your active locations.')
        for template, units, kg in _split(line, quantity, weight_kg):
            rows.append((ReturnLine(order_line=line, batch_number=template.batch_number,
                                    quantity=None if _loose(line.product) else units,
                                    weight_kg=kg if line.product.is_weighed else None, reason=item['reason'],
                                    disposition=disposition, location=location), template))
    return rows


def _create(order, user, kind, rows, *, stop=None, note=''):
    customer_return = CustomerReturn.objects.create(
        company_id=order.company_id, number=_number(order.company_id), order=order, stop=stop, kind=kind,
        status=CustomerReturn.ON_VAN if kind == CustomerReturn.DOOR else CustomerReturn.RECEIVED, note=note,
        created_by=user, received_at=None if kind == CustomerReturn.DOOR else timezone.now())
    for row, _template in rows:
        row.customer_return = customer_return
    ReturnLine.objects.bulk_create(row for row, _template in rows)
    return customer_return


def refuse_at_door(order, stop, user, requested):
    """
    Record goods refused at the door (inside ``runs.deliver``, the order locked). Returns ``{(product id, batch
    number): (units, kg)}`` refused, which the delivery leaves on the van; the return is ``on_van``.
    """
    if not requested:
        return {}
    rows = _lines(order, requested, dispatch_location(order.company_id))
    _create(order, user, CustomerReturn.DOOR, rows, stop=stop)
    refused = {}
    for row, template in rows:
        key = (template.product_id, row.batch_number)
        units, kg = refused.get(key, (0, Decimal('0')))
        refused[key] = (units + (row.quantity or 0), kg + (row.weight_kg or 0))
    return refused


def receive_door_returns(run, van):
    """At run completion: refused goods leave the van, to their location or to WASTE (lots locked by the caller)."""
    waste = virtual_location(run.company_id, Location.WASTE)
    returns = CustomerReturn.objects.select_for_update().filter(stop__run=run, status=CustomerReturn.ON_VAN)
    for customer_return in returns.order_by('pk'):
        for row in customer_return.lines.select_related('order_line__product').order_by('pk'):
            product = row.order_line.product
            lot = StockBatch.objects.select_for_update(of=('self',)).select_related('location').filter(
                product=product, batch_number=row.batch_number, location=van).first()
            if lot is None:
                raise LedgerError(f'{product.name} lot {row.batch_number} is not on {van.code}.')
            units, kg = row.quantity or 0, row.weight_kg if product.is_weighed else None
            if row.disposition == ReturnLine.WASTE:
                move(lot, units, 'outbound', counterpart=waste, weight_kg=kg)
            else:
                relocate(lot, quantity=units, weight_kg=kg, to=row.location)
        customer_return.status, customer_return.received_at = CustomerReturn.RECEIVED, timezone.now()
        customer_return.save(update_fields=['status', 'received_at'])


@transaction.atomic
def record_return(order, user, requested, note=''):
    """Goods a customer gives back later: into their lots at a location, or to WASTE, straight away."""
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in (SalesOrder.DELIVERED, SalesOrder.INVOICED):
        raise ReturnError(f'A {order.get_status_display().lower()} order has nothing delivered to give back.')
    if not requested:
        raise ReturnError('Say what came back.')
    warehouse = Location.objects.filter(pk=order.source_location_id).first()
    rows = _lines(order, requested, warehouse)
    product_ids = sorted({row.order_line.product_id for row, _template in rows})
    list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk').values_list('pk', flat=True))
    customer_return = _create(order, user, CustomerReturn.LATER, rows, note=note)
    dispatch = dispatch_location(order.company_id)
    waste = virtual_location(order.company_id, Location.WASTE)
    try:
        for row, template in rows:
            units = row.quantity or 0
            kg = row.weight_kg if template.product.is_weighed else None
            if row.disposition == ReturnLine.WASTE:
                lot = take_back(template, quantity=units, weight_kg=kg, to=dispatch)
                move(lot, units, 'outbound', counterpart=waste, weight_kg=kg)
            else:
                take_back(template, quantity=units, weight_kg=kg, to=row.location)
    except LedgerError as error:
        raise ReturnError(str(error)) from None
    return customer_return


def delivered(line):
    """``(units, kg)`` that reached the customer on ``line``: picked, less refused at the door."""
    units, kg = door_refused(line)
    return (line.picked_quantity or 0) - units, (line.picked_weight_kg or Decimal('0')) - kg

