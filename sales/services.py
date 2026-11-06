# sales/services.py

"""
Till operations: ringing up a sale, voiding it and cashing up a session.

A sale takes its stock from the product's batches, oldest expiry first (expired batches are never sold),
and records each take as an outbound ``StockMovement`` so demand estimates and the ledger stay correct.
A void puts the same units back with inbound movements. Everything runs in one transaction with the
affected rows locked, so two tills cannot sell the same last unit.
"""

from collections import OrderedDict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from inventory.ledger import LedgerError, move, sellable_locations, take_fefo, take_weighed, virtual_location
from inventory.models import Location, Product, StockBatch
from common.services.settings_service import get_company_setting
from sync.outbox import active_connection, emit
from vat.services import NoVatRate, rate_for, split_inclusive, uk_date

from .models import Sale, SaleAllocation, SaleItem, TillSession

CENTS = Decimal('0.01')
# A scanned label's price further than this from today's price is charged but flagged (G18).
LABEL_TOLERANCE_KEY, DEFAULT_LABEL_TOLERANCE = 'label_price_tolerance_percent', Decimal('10')
# A weight the cashier typed (no label, no scale agent yet) may be at most this per line, and is marked for audit (G19).
MANUAL_WEIGHT_MAX_KEY, DEFAULT_MANUAL_WEIGHT_MAX = 'manual_weight_max_kg', Decimal('25')


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
    # The card terminal (G36): what it should have taken (frozen at close) against its own end-of-day total.
    expected_card = card_total if session.expected_card is None else session.expected_card
    card_variance = None if session.counted_card is None else session.counted_card - expected_card
    return {
        'sales_count': completed.count(),
        'sales_total': cash_total + card_total,
        'cash_total': cash_total,
        'card_total': card_total,
        'voided_count': voided.count(),
        'voided_total': total(voided),
        'expected_cash': expected,
        'variance': variance,
        'expected_card': expected_card,
        'card_variance': card_variance,
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
def close_session(*, session, user, counted_cash, note='', counted_card=None):
    """Cash up: freeze what the drawer and the card terminal should hold, and record what was counted (the terminal's
    end-of-day card total is optional: ``None`` means it was not entered, so there is no card variance)."""
    session = TillSession.objects.select_for_update().get(pk=session.pk)
    if not session.is_open:
        raise serializers.ValidationError({'detail': 'This session is already closed.'})
    session.expected_cash = session.opening_float + (
        session.sales.filter(status=Sale.COMPLETED, payment_method=Sale.CASH).aggregate(t=Sum('total'))['t']
        or Decimal('0.00')
    )
    session.counted_cash = counted_cash
    session.expected_card = (
        session.sales.filter(status=Sale.COMPLETED, payment_method=Sale.CARD).aggregate(t=Sum('total'))['t']
        or Decimal('0.00')
    )
    session.counted_card = counted_card
    session.closed_by = user
    session.closed_at = timezone.now()
    session.note = note
    session.save()
    return session


def manual_weight_limit(company_id):
    """The most kg one typed weight may be (``manual_weight_max_kg``); a value that is not a number above 0 is 25."""
    limit = get_company_setting(company_id, MANUAL_WEIGHT_MAX_KEY, DEFAULT_MANUAL_WEIGHT_MAX, Decimal)
    if not limit.is_finite() or limit <= 0:  # a typo must not stop weighed sales
        return DEFAULT_MANUAL_WEIGHT_MAX
    return limit


@transaction.atomic
def record_sale(*, session, cashier, payment_method, amount_tendered, lines, client_ref=None, loyalty_barcode='',
                card_auth_code='', card_last4=''):
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

    # Merge repeated each products (label lines and weighed lines stay one per scan or weighing) and lock the
    # products in a fixed order so concurrent sales cannot deadlock.
    units, labelled, weighings = OrderedDict(), {}, {}
    for product, quantity, *rest in lines:
        weight, label = (list(rest) + [None, None])[:2]
        if weight is not None:
            weighings.setdefault(product.pk, []).append((quantity, weight, label))
        elif label is not None:
            labelled.setdefault(product.pk, []).append(label)
        else:
            units[product.pk] = units.get(product.pk, 0) + quantity
    wanted = {*units, *labelled, *weighings}
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=wanted).order_by('pk')}

    today = timezone.localdate()
    # A till sells only what is on its branch's sellable locations (shop floor, butchery counter...).
    selling_from = list(sellable_locations(session.till.branch))
    company_id = session.till.branch.company_id
    customer = virtual_location(company_id, Location.CUSTOMER)
    vat_day = uk_date()  # the VAT rate follows the UK calendar day
    tolerance = get_company_setting(company_id, LABEL_TOLERANCE_KEY, DEFAULT_LABEL_TOLERANCE, Decimal)
    if not tolerance.is_finite() or tolerance < 0:  # NaN would break every comparison; a typo must not stop sales
        tolerance = DEFAULT_LABEL_TOLERANCE
    manual_max = manual_weight_limit(company_id)
    plan, problems = [], []
    for pk in sorted(wanted):
        product = products[pk]
        if product.is_weighed != (pk in weighings) or (pk in weighings and pk in {*units, *labelled}):
            problems.append(f'{product.name}: this product changed, scan it again.')  # the kind changed meanwhile
            continue
        batches = _sellable_batches(product, selling_from, today)
        try:
            rate = rate_for(product.vat_code, vat_day)
        except NoVatRate:
            problems.append(f'{product.name}: no VAT rate is set up for "{product.vat_code}". Ask an admin.')
            continue
        if not product.is_weighed:
            quantity = units.get(pk, 0) + len(labelled.get(pk, []))
            available = sum(b.quantity for b in batches)
            if quantity > available:
                problems.append(f'{product.name}: only {available} in date, {quantity} requested.')
                continue
            if units.get(pk):
                plan.append(_Line(product, units[pk], None, (product.price * units[pk]).quantize(CENTS, ROUND_HALF_UP),
                                  rate))
            for label in labelled.get(pk, []):  # one unit each, at the label's price when it has one
                compare = label.label_price is not None
                plan.append(_labelled_line(product, 1, None, label, rate, product.price if compare else None,
                                           tolerance, fallback=product.price))
            continue
        # Weighed: short kg is sold (the gap is booked to ADJUSTMENT); pieces and having some stock are checked.
        if not product.price_per_kg:
            problems.append(f'{product.name}: no price per kg is set. Ask an admin.')
            continue
        if not batches:
            problems.append(f'{product.name}: none in date here.')
            continue
        pieces = sum(quantity for quantity, _, _ in weighings[pk])
        available = sum(b.quantity for b in batches)
        if product.is_catch_weight and pieces > available:
            problems.append(f'{product.name}: only {available} in date, {pieces} requested.')
            continue
        typed = [weight for _, weight, label in weighings[pk] if label is None]
        if any(weight > manual_max for weight in typed):
            limit = f'{manual_max.normalize():f}'  # '2.5000' -> 2.5, '1E+1' -> 10
            problems.extend(f'{product.name}: {weight} kg is over the {limit} kg limit for a typed weight. '
                            'Check it, or weigh it in parts.' for weight in typed if weight > manual_max)
            continue
        for quantity, weight, label in weighings[pk]:
            by_weight = (weight * product.price_per_kg).quantize(CENTS, rounding=ROUND_HALF_UP)
            if label is None:  # typed at the till
                plan.append(_Line(product, quantity, weight, by_weight, rate, manual=True))
            elif label.label_price is None:
                plan.append(_Line(product, quantity, weight, by_weight, rate, label))
            else:  # the label's price is charged; compared with today's £/kg unless the kg came from that price
                plan.append(_labelled_line(product, quantity, weight, label, rate,
                                           None if label.weight_from_price else by_weight, tolerance))
    if problems:
        raise serializers.ValidationError({'items': problems})
    total = sum((line.total for line in plan), Decimal('0.00'))

    if payment_method == Sale.CASH:
        if amount_tendered is None or amount_tendered < total:
            raise serializers.ValidationError({'amount_tendered': f'Cash tendered must be at least {total}.'})
        change = amount_tendered - total
    else:
        amount_tendered, change = total, Decimal('0.00')

    sale = Sale.objects.create(
        session=session, cashier=cashier, payment_method=payment_method,
        total=total, amount_tendered=amount_tendered, change_due=change, client_ref=client_ref,
        loyalty_barcode=loyalty_barcode, card_auth_code=card_auth_code, card_last4=card_last4,
    )
    for line in plan:
        product, quantity, weight = line.product, line.quantity, line.weight
        net, vat = split_inclusive(line.total, line.rate)
        if weight is not None:
            unit_price = product.price_per_kg.quantize(CENTS, ROUND_HALF_UP)
        else:
            unit_price = line.total if line.label is not None else product.price
        item = SaleItem.objects.create(
            sale=sale, product=product, product_name=product.name, quantity=quantity, unit_price=unit_price,
            weight_kg=weight, price_per_kg=None if weight is None else product.price_per_kg, line_total=line.total,
            vat_code=product.vat_code, vat_rate=line.rate, net_amount=net, vat_amount=vat,
            barcode=line.label.raw[:64] if line.label else '',
            label_price=line.label.label_price if line.label else None,
            expected_price=line.expected, label_price_flagged=line.flagged, manual_weight=line.manual,
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


@dataclass(frozen=True)
class _Line:
    product: Product
    quantity: int
    weight: Decimal | None
    total: Decimal
    rate: Decimal
    label: object = None  # sales.labels.Label for a scanned label
    expected: Decimal | None = None
    flagged: bool = False
    manual: bool = False  # the weight was typed by the cashier


def _labelled_line(product, quantity, weight, label, rate, expected, tolerance, fallback=None):
    """A scanned label's line: its printed price is charged (``fallback`` when it has none) and compared with
    ``expected`` (today's price, None when there is nothing independent to compare)."""
    total = label.label_price if label.label_price is not None else fallback
    if expected is None:
        flagged = False
    elif expected == 0:
        flagged = total != 0
    else:
        flagged = abs(total - expected) > expected * tolerance / 100
    return _Line(product, quantity, weight, total, rate, label, expected, flagged)


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
    return reverse_sale(sale, user=user, reason=reason)


def reverse_sale(sale, *, user, reason='', voided_at=None, notify_loyalty=True):
    """
    Put a (locked) sale's stock back into the batches it came from and mark it voided. The checks are the caller's:
    ``void_sale`` (online) and an offline void replayed at sync (G28), which may land on a session closed since.
    """
    allocations = list(
        SaleAllocation.objects.filter(item__sale=sale).select_related('batch').order_by('batch_id')
    )
    customer = virtual_location(sale.session.till.branch.company_id, Location.CUSTOMER)
    for allocation in allocations:  # back into the batch (and so the location) each unit came from
        batch = (StockBatch.objects.select_for_update(of=('self',)).select_related('location')
                 .get(pk=allocation.batch_id))
        move(batch, allocation.quantity, 'inbound', counterpart=customer, weight_kg=allocation.weight_kg)
    sale.status = Sale.VOIDED
    sale.voided_at = voided_at or timezone.now()
    sale.voided_by = user
    sale.void_reason = reason
    sale.save(update_fields=['status', 'voided_at', 'voided_by', 'void_reason'])
    if sale.loyalty_barcode and notify_loyalty:  # voided at most once (checked by the caller): reverses once
        connection = active_connection(sale.session.till.branch.company_id)
        if connection:
            emit(connection, 'loyalty.reverse', {'order_reference': _loyalty_reference(sale)})
    return sale
