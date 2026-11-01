# sales/services.py

"""
Till operations: ringing up a sale, voiding it and cashing up a session.

A sale takes its stock from the product's batches, oldest expiry first (expired batches are never sold),
and records each take as an outbound ``StockMovement`` so demand estimates and the ledger stay correct.
A void puts the same units back with inbound movements. Everything runs in one transaction with the
affected rows locked, so two tills cannot sell the same last unit.
"""

from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from inventory.ledger import LedgerError, move, sellable_locations, take_fefo, take_weighed, virtual_location
from inventory.models import Location, Product, StockBatch
from sync.outbox import active_connection, emit
from vat.services import NoVatRate, rate_for, split_inclusive, uk_date

from .models import Sale, SaleAllocation, SaleItem, TillSession

CENTS = Decimal('0.01')


def vat_by_rate(items):
    """
    VAT totals by (code, rate) for sale lines, as sums of the rounded lines: ``[{vat_code, rate, net, vat, gross}]``,
    lowest rate first. Lines sold before VAT was recorded are left out. Works on prefetched lines (no queries).
    """
    groups = {}
    for item in items:
        if item.vat_rate is None:
            continue
        group = groups.setdefault(
            (item.vat_rate, item.vat_code),
            {'vat_code': item.vat_code, 'rate': item.vat_rate, 'net': Decimal('0.00'), 'vat': Decimal('0.00'),
             'gross': Decimal('0.00')},
        )
        group['net'] += item.net_amount
        group['vat'] += item.vat_amount
        group['gross'] += item.line_total
    return [groups[key] for key in sorted(groups)]


def session_summary(session):
    """Takings for a session. Voided sales are listed separately and excluded from every total."""
    completed = session.sales.filter(status=Sale.COMPLETED)

    def total(queryset):
        return queryset.aggregate(total=Sum('total'))['total'] or Decimal('0.00')

    cash_total = total(completed.filter(payment_method=Sale.CASH))
    card_total = total(completed.filter(payment_method=Sale.CARD))
    voided = session.sales.filter(status=Sale.VOIDED)
    expected = session.expected_cash
    if expected is None:
        expected = session.opening_float + cash_total
    variance = None if session.counted_cash is None else session.counted_cash - expected
    return {
        'sales_count': completed.count(),
        'sales_total': cash_total + card_total,
        'cash_total': cash_total,
        'card_total': card_total,
        'voided_count': voided.count(),
        'voided_total': total(voided),
        'expected_cash': expected,
        'variance': variance,
        'vat_by_rate': vat_by_rate(SaleItem.objects.filter(sale__in=completed)),
        'vat_not_recorded_total': SaleItem.objects.filter(sale__in=completed, vat_rate__isnull=True)
        .aggregate(t=Sum('line_total'))['t'] or Decimal('0.00'),
    }


@transaction.atomic
def open_session(*, till, user, opening_float):
    if not till.is_active:
        raise serializers.ValidationError({'till': 'This till is not active.'})
    if TillSession.objects.select_for_update().filter(till=till, closed_at__isnull=True).exists():
        raise serializers.ValidationError({'till': 'This till already has an open session.'})
    return TillSession.objects.create(till=till, opened_by=user, opening_float=opening_float)


@transaction.atomic
def close_session(*, session, user, counted_cash, note=''):
    session = TillSession.objects.select_for_update().get(pk=session.pk)
    if not session.is_open:
        raise serializers.ValidationError({'detail': 'This session is already closed.'})
    session.expected_cash = session.opening_float + (
        session.sales.filter(status=Sale.COMPLETED, payment_method=Sale.CASH).aggregate(t=Sum('total'))['t']
        or Decimal('0.00')
    )
    session.counted_cash = counted_cash
    session.closed_by = user
    session.closed_at = timezone.now()
    session.note = note
    session.save()
    return session


@transaction.atomic
def record_sale(*, session, cashier, payment_method, amount_tendered, lines, client_ref=None, loyalty_barcode=''):
    """
    ``lines`` is a list of ``(Product, quantity)`` or ``(Product, quantity, weight_kg)`` (products sold by weight:
    one line per weighing, priced ``weight_kg × price_per_kg``). Prices come from the product, never from the client.

    Returns ``(sale, created)``. A repeated ``client_ref`` on the same session returns the original sale with
    ``created=False``, so a retry after a lost response cannot charge twice (or award loyalty points twice).

    With a ``loyalty_barcode`` and an active storefront connection, a ``loyalty.award`` event is written in the
    same transaction and sent once it commits; the sale never waits on the storefront.
    """
    session = TillSession.objects.select_for_update().get(pk=session.pk)
    if client_ref is not None:
        existing = Sale.objects.filter(session=session, client_ref=client_ref).first()
        if existing:
            return existing, False
    if not session.is_open:
        raise serializers.ValidationError({'session': 'This session is closed. Open the till to sell.'})

    # Merge repeated each products (weighed lines stay one per weighing) and lock the products in a fixed order so
    # concurrent sales cannot deadlock.
    wanted, weighings = OrderedDict(), {}
    for product, quantity, *weight in lines:
        weight = weight[0] if weight else None
        if weight is None:
            wanted[product.pk] = (product, wanted.get(product.pk, (product, 0))[1] + quantity)
        else:
            wanted.setdefault(product.pk, (product, 0))
            weighings.setdefault(product.pk, []).append((quantity, weight))
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=wanted).order_by('pk')}

    today = timezone.localdate()
    # A till sells only what is on its branch's sellable locations (shop floor, butchery counter...).
    selling_from = list(sellable_locations(session.till.branch))
    customer = virtual_location(session.till.branch.company_id, Location.CUSTOMER)
    vat_day = uk_date()  # the VAT rate follows the UK calendar day
    plan, problems = [], []
    for pk in sorted(wanted):
        product = products[pk]
        if product.is_weighed != (pk in weighings):  # the kind changed since the line was checked
            problems.append(f'{product.name}: this product changed, scan it again.')
            continue
        batches = _sellable_batches(product, selling_from, today)
        try:
            rate = rate_for(product.vat_code, vat_day)
        except NoVatRate:
            problems.append(f'{product.name}: no VAT rate is set up for "{product.vat_code}". Ask an admin.')
            continue
        if not product.is_weighed:
            quantity = wanted[pk][1]
            available = sum(b.quantity for b in batches)
            if quantity > available:
                problems.append(f'{product.name}: only {available} in date, {quantity} requested.')
                continue
            line_total = (product.price * quantity).quantize(CENTS, rounding=ROUND_HALF_UP)
            plan.append((product, quantity, None, line_total, rate))
            continue
        # Weighed: short kg is sold (the gap is booked to ADJUSTMENT); pieces and having some stock are checked.
        if not product.price_per_kg:
            problems.append(f'{product.name}: no price per kg is set. Ask an admin.')
            continue
        if not batches:
            problems.append(f'{product.name}: none in date here.')
            continue
        pieces = sum(quantity for quantity, _ in weighings[pk])
        available = sum(b.quantity for b in batches)
        if product.is_catch_weight and pieces > available:
            problems.append(f'{product.name}: only {available} in date, {pieces} requested.')
            continue
        for quantity, weight in weighings[pk]:
            line_total = (weight * product.price_per_kg).quantize(CENTS, rounding=ROUND_HALF_UP)
            plan.append((product, quantity, weight, line_total, rate))
    if problems:
        raise serializers.ValidationError({'items': problems})
    total = sum((line_total for *_, line_total, _rate in plan), Decimal('0.00'))

    if payment_method == Sale.CASH:
        if amount_tendered is None or amount_tendered < total:
            raise serializers.ValidationError({'amount_tendered': f'Cash tendered must be at least {total}.'})
        change = amount_tendered - total
    else:
        amount_tendered, change = total, Decimal('0.00')

    sale = Sale.objects.create(
        session=session, cashier=cashier, payment_method=payment_method,
        total=total, amount_tendered=amount_tendered, change_due=change, client_ref=client_ref,
        loyalty_barcode=loyalty_barcode,
    )
    for product, quantity, weight, line_total, rate in plan:
        net, vat = split_inclusive(line_total, rate)
        item = SaleItem.objects.create(
            sale=sale, product=product, product_name=product.name, quantity=quantity,
            unit_price=product.price if weight is None else product.price_per_kg.quantize(CENTS, ROUND_HALF_UP),
            weight_kg=weight, price_per_kg=None if weight is None else product.price_per_kg, line_total=line_total,
            vat_code=product.vat_code, vat_rate=rate, net_amount=net, vat_amount=vat,
        )
        if weight is None:
            allocations, shortfall = take_fefo(product, quantity, locations=selling_from, destination=customer,
                                               today=today)
            if shortfall:  # cannot happen after the check above (rows are locked); never oversell
                raise serializers.ValidationError({'items': [f'{product.name}: stock changed, try again.']})
            allocations = [(batch, taken, None) for batch, taken in allocations]
        else:
            try:
                allocations = take_weighed(product, quantity=quantity if product.is_catch_weight else 0,
                                           weight_kg=weight, locations=selling_from, destination=customer,
                                           today=today)
            except LedgerError:  # checked above with the rows locked; never oversell pieces
                raise serializers.ValidationError({'items': [f'{product.name}: stock changed, try again.']}) from None
        for batch, taken, kg in allocations:
            SaleAllocation.objects.create(item=item, batch=batch, quantity=taken, weight_kg=kg)
    if loyalty_barcode:
        connection = active_connection(session.till.branch.company_id)
        if connection:
            emit(connection, 'loyalty.award', {
                'barcode': loyalty_barcode, 'amount': str(total), 'order_reference': _loyalty_reference(sale),
            })
    return sale, True


def _sellable_batches(product, locations, today):
    """In-date batches holding stock (kg for loose products, pieces otherwise) at ``locations``, locked, FEFO order."""
    held = {'weight_kg__gt': 0} if product.is_weighed and not product.is_catch_weight else {'quantity__gt': 0}
    return list(
        StockBatch.objects.select_for_update()
        .filter(product=product, location__in=locations, expiration_date__gte=today, **held)
        .order_by('expiration_date', 'receive_date', 'pk')
    )


def _loyalty_reference(sale):
    return f'iq-sale-{sale.client_ref or sale.pk}'


@transaction.atomic
def void_sale(*, sale, user, reason=''):
    sale = Sale.objects.select_for_update().select_related('session').get(pk=sale.pk)
    if sale.status == Sale.VOIDED:
        raise serializers.ValidationError({'detail': 'This sale is already voided.'})
    if not TillSession.objects.get(pk=sale.session_id).is_open:
        raise serializers.ValidationError({'detail': 'The session is closed, so this sale can no longer be voided.'})
    allocations = list(
        SaleAllocation.objects.filter(item__sale=sale).select_related('batch').order_by('batch_id')
    )
    customer = virtual_location(sale.session.till.branch.company_id, Location.CUSTOMER)
    for allocation in allocations:  # back into the batch (and so the location) each unit came from
        batch = (StockBatch.objects.select_for_update(of=('self',)).select_related('location')
                 .get(pk=allocation.batch_id))
        move(batch, allocation.quantity, 'inbound', counterpart=customer, weight_kg=allocation.weight_kg)
    sale.status = Sale.VOIDED
    sale.voided_at = timezone.now()
    sale.voided_by = user
    sale.void_reason = reason
    sale.save(update_fields=['status', 'voided_at', 'voided_by', 'void_reason'])
    if sale.loyalty_barcode:  # a sale is voided at most once (checked above), so this reverses once
        connection = active_connection(sale.session.till.branch.company_id)
        if connection:
            emit(connection, 'loyalty.reverse', {'order_reference': _loyalty_reference(sale)})
    return sale
