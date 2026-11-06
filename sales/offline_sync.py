# sales/offline_sync.py

"""
Offline sales replayed on the server (G28, ADR 0001). A till that couldn't reach the server kept its sales (G27) and
sends them here when it reconnects.

The customer paid what the till charged, so a sale is never refused for being different from today's view, and the
till's reading of each line (its product, kg and charged amount) is what is booked. Each line gets VAT at the rate of
the sale's UK date; stock is taken FEFO now (in date on the sale date), and what isn't there comes through a
``SHORT-`` batch from ADJUSTMENT. Every difference (price, stock, a label that no longer reads as sold, a product
changed since, a typed weight over the limit, cash short of the total, a session closed since, an unknown cashier)
is a ``SaleException`` for a manager. A sale voided on the till is recorded as voided and moves no stock (the goods
never left). A sale is idempotent by its ``client_ref`` within the company: sending it again changes nothing, except
that a void made on the till is applied to it.

Only a sale that can't be recorded at all (not this company's session or product, no VAT rate on its date, no
location to sell from, or an unexpected failure) comes back as ``error``; it stays on the till for a retry, and the
sales after it are still recorded.
"""

import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from common.services.settings_service import get_company_setting
from inventory.ledger import sellable_locations, take_fefo, take_short, take_weighed, virtual_location
from inventory.models import Location, Product, StockBatch
from sync.outbox import active_connection, emit
from vat.services import UK, NoVatRate, rate_for, split_inclusive

from .labels import read_label
from .models import Sale, SaleAllocation, SaleException, SaleItem, TillSession
from .serializers import LAST4
from .services import (
    DEFAULT_LABEL_TOLERANCE, LABEL_TOLERANCE_KEY, _loyalty_reference, manual_weight_limit, reverse_sale,
)

logger = logging.getLogger(__name__)
CENTS, KG = Decimal('0.01'), Decimal('0.001')
CLOCK_SKEW = timedelta(minutes=5)


class OfflineSaleError(Exception):
    """This sale can't be recorded yet (it stays on the till and is sent again); ``detail`` says why."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def sync_offline_sales(user, sales):
    """Record ``sales`` (validated dicts) oldest first, each on its own; returns one result per sale, in that order."""
    results = []
    for data in sorted(sales, key=lambda sale: (sale['created_at'], sale.get('offline_number', ''))):
        ref = str(data['client_ref'])
        try:
            with transaction.atomic():
                sale, created, kinds = record_offline_sale(user, data)
        except OfflineSaleError as error:
            results.append({'client_ref': ref, 'status': 'error', 'detail': error.detail, 'exceptions': []})
            continue
        except IntegrityError as error:  # the only unique rule left after the client_ref check
            if 'offline_number' not in str(error):
                raise
            results.append({'client_ref': ref, 'status': 'error', 'exceptions': [],
                            'detail': 'This offline number was already used on this session by another sale.'})
            continue
        except Exception:  # never let one sale block the till's queue; its savepoint rolled back
            logger.exception('Offline sale %s could not be recorded', ref)
            results.append({'client_ref': ref, 'status': 'error', 'exceptions': [],
                            'detail': 'The server could not record this sale; it will be tried again.'})
            continue
        results.append({'client_ref': ref, 'status': 'recorded' if created else 'duplicate', 'sale': sale.pk,
                        'receipt_number': sale.receipt_number, 'exceptions': kinds})
    return results


def _company_user(company, pk):
    return get_user_model().objects.filter(pk=pk, company=company).first() if pk else None


def _apply_void(sale, company, user, void):
    """A void made on the till for a sale the server already had (its answer was lost): reverse it now."""
    sale = Sale.objects.select_for_update().select_related('session__till__branch').get(pk=sale.pk)
    if sale.status != Sale.VOIDED:
        voider = _company_user(company, void.get('voided_by')) or user
        reverse_sale(sale, user=voider, reason=void.get('reason', ''), voided_at=void.get('voided_at'))
    return sale


def _reading(product, item, company, day):
    """
    The till's reading of a line, checked against the label again: ``(weight, label, problem)``. The till's product
    and kg stay; a label that no longer reads as sold is reported, never re-booked as something else.
    """
    weight = item.get('weight_kg')
    if not item.get('barcode'):
        return weight, None, ''
    try:
        label = read_label(company, item['barcode'], day)
    except serializers.ValidationError as error:
        return weight, None, '; '.join(str(message) for message in error.detail)
    if label.product.pk != product.pk:
        return weight, None, f'The label now reads as {label.product.name}; booked as sold ({product.name}).'
    return (label.weight_kg if label.weight_kg is not None else weight), label, ''


def _expected(product, quantity, weight, label):
    """What the server charges for the line today, or None when there is no independent price."""
    if label is not None and label.label_price is not None:
        return label.label_price
    if weight is not None:
        if not product.is_weighed or not product.price_per_kg:
            return None
        return (weight * product.price_per_kg).quantize(CENTS, ROUND_HALF_UP)
    if product.is_weighed:
        return None
    return (product.price * quantity).quantize(CENTS, ROUND_HALF_UP)


def _label_flags(product, quantity, weight, label, charged, tolerance):
    """``(expected_price, flagged)`` for a label carrying a price, compared like an online sale (G18)."""
    if label is None or label.label_price is None:
        return None, False
    if label.weight_from_price or (weight is not None and not product.price_per_kg):
        return None, False
    compare = (product.price * quantity if weight is None else (weight * product.price_per_kg)).quantize(CENTS,
                                                                                                      ROUND_HALF_UP)
    if compare == 0:
        return compare, charged != 0
    return compare, abs(charged - compare) > compare * tolerance / 100


def record_offline_sale(user, data):
    """
    Record one offline sale for ``user``'s company. Returns ``(sale, created, exception kinds)``; raises
    ``OfflineSaleError`` when it can't be recorded — nothing is written then.
    """
    company = user.company
    existing = (Sale.objects.filter(client_ref=data['client_ref'], session__till__branch__company=company)
                .select_related('session').first())
    if existing:
        if data.get('void'):
            existing = _apply_void(existing, company, user, data['void'])
        return existing, False, []
    session = (TillSession.objects.select_for_update().select_related('till__branch')
               .filter(pk=data['session'], till__branch__company=company).first())
    if session is None:
        raise OfflineSaleError('Till session not found.')
    if Sale.objects.filter(session=session, client_ref=data['client_ref']).exists():  # sent twice at once
        return Sale.objects.get(session=session, client_ref=data['client_ref']), False, []

    sold_at = min(data['created_at'], timezone.now() + CLOCK_SKEW)  # a till clock ahead must not pick future rates
    day = timezone.localtime(sold_at, UK).date()
    branch = session.till.branch
    locations = list(sellable_locations(branch))
    voided = bool(data.get('void'))
    if not locations and not voided:
        raise OfflineSaleError(f'{branch.name} has no location to sell from.')
    customer = virtual_location(company.pk, Location.CUSTOMER)
    manual_max = manual_weight_limit(company.pk)
    tolerance = get_company_setting(company.pk, LABEL_TOLERANCE_KEY, DEFAULT_LABEL_TOLERANCE, Decimal)
    if not tolerance.is_finite() or tolerance < 0:
        tolerance = DEFAULT_LABEL_TOLERANCE
    wanted = sorted({item['product'] for item in data['items']})
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=wanted, company=company)
                .order_by('pk')}  # pk order, as online sales lock them: no deadlock between the two
    if len(products) != len(wanted):
        raise OfflineSaleError('A product on this sale was not found.')

    lines, exceptions = [], []
    for item in data['items']:
        product = products[item['product']]
        weight, label, label_problem = _reading(product, item, company, day)
        quantity = item.get('quantity') or 1
        try:
            rate = rate_for(product.vat_code, day)
        except NoVatRate:
            raise OfflineSaleError(f'{product.name}: no VAT rate for "{product.vat_code}" on {day:%d/%m/%Y}.') from None
        lines.append((product, quantity, weight, label, label_problem, item, rate))

    cashier = _company_user(company, data.get('cashier'))
    if cashier is None:
        cashier = user
        exceptions.append((None, SaleException.CASHIER,
                           {'detail': 'The cashier on this sale was not found; recorded under who sent it.'}))
    total = sum((item['charged'] for item in data['items']), Decimal('0.00'))
    cash = data['payment_method'] == Sale.CASH
    tendered = data.get('amount_tendered') if cash else total
    tendered = total if tendered is None else tendered
    if cash and tendered < total:
        exceptions.append((None, SaleException.TENDER, {'charged': total, 'expected': tendered,
                                                        'detail': f'Cash tendered {tendered} is below {total}.'}))
    # Card details as the till kept them (never a reason to refuse a paid sale): the code in capitals, a last 4 only
    # when it is 4 digits.
    last4 = data.get('card_last4', '').strip()
    card = {
        'card_auth_code': data.get('card_auth_code', '').strip().upper() if not cash else '',
        'card_last4': last4 if not cash and LAST4.fullmatch(last4) else '',
    }
    sale = Sale.objects.create(
        session=session, cashier=cashier, payment_method=data['payment_method'], total=total,
        amount_tendered=tendered, change_due=max(tendered - total, Decimal('0.00')) if cash else Decimal('0.00'),
        client_ref=data['client_ref'], loyalty_barcode=data.get('loyalty_barcode', ''),
        offline_number=data.get('offline_number', ''), sold_at=sold_at, **card,
    )
    if not session.is_open:
        exceptions.append((None, SaleException.SESSION_CLOSED,
                           {'detail': 'The session was closed before this sale was sent; its cash-up does not '
                                      'include it.'}))

    for product, quantity, weight, label, label_problem, item, rate in lines:
        charged = item['charged']
        changed = product.is_weighed != (weight is not None)  # sold by each/weight then, the other way now
        typed = weight is not None and not item.get('barcode')
        net, vat = split_inclusive(charged, rate)
        expected_price, flagged = _label_flags(product, quantity, weight, label, charged, tolerance)
        per_kg = product.price_per_kg if weight is not None and product.price_per_kg else None
        line = SaleItem.objects.create(
            sale=sale, product=product, product_name=product.name, quantity=quantity,
            unit_price=(per_kg.quantize(CENTS, ROUND_HALF_UP) if per_kg is not None
                        else (charged / quantity).quantize(CENTS, ROUND_HALF_UP)),
            weight_kg=weight, price_per_kg=per_kg, line_total=charged, vat_code=product.vat_code, vat_rate=rate,
            net_amount=net, vat_amount=vat, barcode=item.get('barcode', '')[:64],
            label_price=label.label_price if label else None, expected_price=expected_price,
            label_price_flagged=flagged, manual_weight=typed,
        )
        if changed:
            exceptions.append((line, SaleException.PRODUCT_CHANGED, {
                'product': product,
                'detail': (f'{product.name} is sold by weight now; it was sold by each.' if product.is_weighed
                           else f'{product.name} is sold by each now; it was sold by weight.')}))
        if label_problem:
            exceptions.append((line, SaleException.LABEL, {'product': product, 'detail': label_problem[:255]}))
        expected = None if changed else _expected(product, quantity, weight, label)
        if expected is not None and expected != charged:
            exceptions.append((line, SaleException.PRICE, {'product': product, 'expected': expected,
                                                           'charged': charged}))
        if typed and not changed and weight > manual_max:
            exceptions.append((line, SaleException.WEIGHT_LIMIT, {
                'product': product, 'weight_kg': weight,
                'detail': f'{weight} kg typed; the limit is {manual_max.normalize():f} kg.'}))
        if voided:
            continue  # voided on the till: the goods never left, so no stock moves
        if changed and product.is_weighed:  # no kg to take; a manager counts it
            exceptions.append((line, SaleException.STOCK, {'product': product, 'quantity': quantity,
                                                           'detail': 'No kg was recorded, so no stock was taken.'}))
            continue
        allocations, short_units, short_kg = _take(product, quantity, None if changed else weight, locations,
                                                   customer, day, sale)
        for batch, taken, kg in allocations:
            SaleAllocation.objects.create(item=line, batch=batch, quantity=taken, weight_kg=kg)
        if short_units or short_kg:
            exceptions.append((line, SaleException.STOCK, {
                'product': product, 'quantity': short_units or None, 'weight_kg': short_kg or None,
                'detail': 'Sold offline without enough stock here; booked from adjustment.'}))

    for line, kind, fields in exceptions:
        SaleException.objects.create(sale=sale, item=line, kind=kind, **fields)

    if voided:
        void = data['void']
        sale.status, sale.void_reason = Sale.VOIDED, void.get('reason', '')
        sale.voided_at = void.get('voided_at') or timezone.now()
        sale.voided_by = _company_user(company, void.get('voided_by')) or user
        sale.save(update_fields=['status', 'void_reason', 'voided_at', 'voided_by'])
    elif sale.loyalty_barcode:
        connection = active_connection(company.pk)
        if connection:
            emit(connection, 'loyalty.award', {'barcode': sale.loyalty_barcode, 'amount': str(total),
                                               'order_reference': _loyalty_reference(sale)})
    return sale, True, sorted({kind for _, kind, _ in exceptions})


def _short(product, quantity, weight, locations, customer, day, sale):
    return take_short(product, quantity=quantity, weight_kg=weight, location=locations[0], destination=customer,
                      reference=sale.receipt_number, expiry=day)


def _locked(product, locations, day, held):
    return list(StockBatch.objects.select_for_update(of=('self',))
                .filter(product=product, location__in=locations, expiration_date__gte=day, **{f'{held}__gt': 0}))


def _take(product, quantity, weight, locations, customer, day, sale):
    """Take a line's stock FEFO (in date on the sale ``day``); returns ``(allocations, units short, kg short)``."""
    if not product.is_weighed:
        allocations, short = take_fefo(product, quantity, locations=locations, destination=customer, today=day)
        allocations = [(batch, taken, None) for batch, taken in allocations]
        if short:
            allocations.append(_short(product, short, None, locations, customer, day, sale))
        return allocations, short, Decimal('0')
    if not product.is_catch_weight:
        held = sum((b.weight_kg for b in _locked(product, locations, day, 'weight_kg')), Decimal('0.000'))
        if held <= 0:
            return [_short(product, 0, weight, locations, customer, day, sale)], 0, weight
        # take_weighed books a gap from ADJUSTMENT into the last batch itself; it is reported the same way.
        allocations = take_weighed(product, quantity=0, weight_kg=weight, locations=locations, destination=customer,
                                   today=day)
        return allocations, 0, max(weight - held, Decimal('0.000'))
    pieces = sum(b.quantity for b in _locked(product, locations, day, 'quantity'))
    if pieces >= quantity:
        return take_weighed(product, quantity=quantity, weight_kg=weight, locations=locations, destination=customer,
                            today=day), 0, Decimal('0')
    allocations, kg_taken = [], Decimal('0.000')
    if pieces:
        kg_taken = (weight * pieces / quantity).quantize(KG, ROUND_HALF_UP)
        allocations = take_weighed(product, quantity=pieces, weight_kg=kg_taken, locations=locations,
                                   destination=customer, today=day)
    allocations.append(_short(product, quantity - pieces, weight - kg_taken, locations, customer, day, sale))
    return allocations, quantity - pieces, weight - kg_taken
