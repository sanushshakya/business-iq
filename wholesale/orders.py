"""
Sales orders (G40): the status machine and confirmation.

draft -> confirmed -> picking -> picked -> dispatched -> delivered -> invoiced; cancelled from draft, confirmed or
picking (a reason is required). Confirming fixes each line's trade price from ``pricing.resolve_prices`` on the
delivery date, with the VAT rate in force that day, and the estimated net and VAT (half up to the penny); it is
never re-done. picking onwards are moved by picking, delivery and invoicing (G42-G44) through ``advance``.

G41: confirming also reserves each line at the order's warehouse (``source_location``), FEFO, without moving
stock; a line that can't be covered stays short (``allocate`` tops it up later). Cancelling releases.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from inventory.models import Location
from inventory.reservations import release, reserve
from tenants.models import Company
from vat.services import HUNDRED, PENNY, NoVatRate, rate_for

from .models import KG, SalesOrder, SalesOrderLine, TradeCustomer
from .pricing import resolve_prices

S = SalesOrder
TRANSITIONS = {
    S.DRAFT: {S.CONFIRMED, S.CANCELLED},
    S.CONFIRMED: {S.PICKING, S.CANCELLED},
    S.PICKING: {S.PICKED, S.CANCELLED},
    S.PICKED: {S.DISPATCHED},
    S.DISPATCHED: {S.DELIVERED},
    S.DELIVERED: {S.INVOICED},
    S.INVOICED: set(),
    S.CANCELLED: set(),
}
CANCELLABLE = {status for status, moves in TRANSITIONS.items() if S.CANCELLED in moves}


class OrderStateError(ValueError):
    """The order can't make this move; ``detail`` is for the API."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def next_order_number(company):
    """The company's next order number; locks the company row (call inside a transaction)."""
    Company.objects.select_for_update().get(pk=company.pk)
    last = SalesOrder.objects.filter(company=company).order_by('-number').values_list('number', flat=True).first()
    return (last or 0) + 1


def _locked(order, to):
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if to not in TRANSITIONS[order.status]:
        raise OrderStateError(f'A {order.get_status_display().lower()} order can\'t become {to}.')
    return order


def _ordered_amount(line):
    """What the price break and the line total use: units (each), kg (loose), or estimated kg (catch-weight)."""
    product = line.product
    if not product.is_weighed:
        return line.quantity
    if product.is_catch_weight:
        if not product.nominal_weight_kg:
            raise OrderStateError(f'{product.name} has no nominal weight to estimate its kg from.')
        line.weight_kg = (line.quantity * product.nominal_weight_kg).quantize(KG, rounding=ROUND_HALF_UP)
    return line.weight_kg


@transaction.atomic
def confirm(order, user):
    """Price the lines (once) and confirm. Refused for a customer on hold or closed, or a line with no price."""
    order = _locked(order, S.CONFIRMED)
    # Locked like a customer edit, so a hold put on at the same moment is seen (or waits for this).
    customer = TradeCustomer.objects.select_for_update().get(pk=order.customer_id)
    if customer.status != TradeCustomer.ACTIVE:
        raise OrderStateError(f'The customer is {customer.get_status_display().lower()}; it can\'t be confirmed.')
    lines = list(order.lines.select_related('product').order_by('id'))
    if not lines:
        raise OrderStateError('An order needs at least one line.')
    source = _source(order)
    amounts = [_ordered_amount(line) for line in lines]
    prices = resolve_prices(customer, [(line.product, amount) for line, amount in zip(lines, amounts, strict=True)],
                            on=order.delivery_date)
    unpriced = [line.product.name for line, price in zip(lines, prices, strict=True) if price is None]
    if unpriced:
        raise OrderStateError(f'No trade price for: {", ".join(unpriced)}.')
    rates = {}
    for line, amount, price in zip(lines, amounts, prices, strict=True):
        code = line.product.vat_code
        if code not in rates:
            try:
                rates[code] = rate_for(code, order.delivery_date)
            except NoVatRate as error:
                raise OrderStateError(str(error)) from None
        line.unit_price, line.price_source = price.unit_price, price.source
        line.price_list = price.price_list
        line.price_list_name = price.price_list.name if price.price_list else ''
        line.min_quantity = price.min_quantity
        line.vat_code, line.vat_rate = code, rates[code]
        line.estimated_net = (amount * price.unit_price).quantize(PENNY, rounding=ROUND_HALF_UP)
        line.estimated_vat = (line.estimated_net * line.vat_rate / HUNDRED).quantize(PENNY, rounding=ROUND_HALF_UP)
    SalesOrderLine.objects.bulk_update(lines, [
        'weight_kg', 'unit_price', 'price_source', 'price_list', 'price_list_name', 'min_quantity', 'vat_code',
        'vat_rate', 'estimated_net', 'estimated_vat',
    ])
    order.estimated_net = sum((line.estimated_net for line in lines), Decimal('0'))
    order.estimated_vat = sum((line.estimated_vat for line in lines), Decimal('0'))
    order.estimated_gross = order.estimated_net + order.estimated_vat
    order.status, order.confirmed_by, order.confirmed_at = S.CONFIRMED, user, timezone.now()
    order.save()
    for line in lines:  # what the warehouse can't cover stays short
        reserve(line, source)
    return order


def _source(order):
    source = Location.objects.filter(pk=order.source_location_id).first()
    if source is None:
        raise OrderStateError('Choose the warehouse the order is picked from.')
    if source.type != Location.WAREHOUSE or not source.is_active:
        raise OrderStateError(f'{source.code} is not an active warehouse.')
    return source


@transaction.atomic
def allocate(order, user):
    """Top up a confirmed (or picking) order's short lines from free stock at its warehouse, FEFO."""
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in (S.CONFIRMED, S.PICKING):
        raise OrderStateError(f'A {order.get_status_display().lower()} order holds no stock to top up.')
    source = _source(order)
    for line in order.lines.select_related('product').order_by('id'):
        reserve(line, source)
    return order


@transaction.atomic
def cancel(order, user, reason):
    reason = (reason or '').strip()
    if not reason:
        raise OrderStateError('Say why the order is cancelled.')
    order = _locked(order, S.CANCELLED)
    order.status, order.cancelled_by, order.cancelled_at = S.CANCELLED, user, timezone.now()
    order.cancel_reason = reason[:255]
    order.save()
    release(order)
    return order


@transaction.atomic
def advance(order, to):
    """The later steps (picking onwards), for G41-G44; confirm and cancel have their own functions."""
    if to in (S.CONFIRMED, S.CANCELLED):
        raise OrderStateError(f'Use {"confirm" if to == S.CONFIRMED else "cancel"} for that.')
    order = _locked(order, to)
    order.status = to
    order.save(update_fields=['status', 'updated_at'])
    return order
