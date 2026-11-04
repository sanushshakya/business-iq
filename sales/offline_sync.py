# sales/offline_sync.py

"""
Offline sales replayed on the server (G28, ADR 0001). A till that couldn't reach the server kept its sales (G27) and
sends them here when it reconnects.

The customer paid what the till charged, so a sale is never refused for being different from today's view: each
line is booked at its charged price with VAT at the rate of the sale's UK date; stock is taken FEFO now (as it was
in date on the sale date), and what isn't there comes through a ``SHORT-`` batch from ADJUSTMENT. Every difference
(price, stock, an unreadable label, a typed weight over the limit, a session closed since, an unknown cashier) is a
``SaleException`` for a manager. A sale is idempotent by ``(session, client_ref)``: sending it again changes nothing.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from inventory.ledger import LedgerError, sellable_locations, take_fefo, take_short, take_weighed, virtual_location
from inventory.models import Location, Product, StockBatch
from sync.outbox import active_connection, emit
from vat.services import UK, NoVatRate, rate_for, split_inclusive

from .labels import read_label
from .models import Sale, SaleAllocation, SaleException, SaleItem, TillSession
from .services import _loyalty_reference, manual_weight_limit, reverse_sale

CENTS, KG = Decimal('0.01'), Decimal('0.001')


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
        results.append({'client_ref': ref, 'status': 'recorded' if created else 'duplicate', 'sale': sale.pk,
                        'receipt_number': sale.receipt_number, 'exceptions': kinds})
    return results


def _company_user(company, pk):
    return get_user_model().objects.filter(pk=pk, company=company).first() if pk else None


def _expected(product, quantity, weight, label):
    """What the server charges for the line today (None when there is nothing to compare: an unreadable label)."""
    if label is not None:
        if label.label_price is not None:
            return label.label_price
        return (label.weight_kg * product.price_per_kg).quantize(CENTS, ROUND_HALF_UP)
    if weight is not None:
        return (weight * (product.price_per_kg or 0)).quantize(CENTS, ROUND_HALF_UP)
    return (product.price * quantity).quantize(CENTS, ROUND_HALF_UP)


def record_offline_sale(user, data):
    """
    Record one offline sale for ``user``'s company. Returns ``(sale, created, exception kinds)``; raises
    ``OfflineSaleError`` when it can't be recorded (not this company's session or product, or no VAT rate on the
    sale's date) — nothing is written then.
    """
    company = user.company
    session = (TillSession.objects.select_for_update().select_related('till__branch')
               .filter(pk=data['session'], till__branch__company=company).first())
    if session is None:
        raise OfflineSaleError('Till session not found.')
    existing = Sale.objects.filter(session=session, client_ref=data['client_ref']).first()
    if existing:
        return existing, False, []

    sold_at = data['created_at']
    day = timezone.localtime(sold_at, UK).date()
    branch = session.till.branch
    locations = list(sellable_locations(branch))
    if not locations:
        raise OfflineSaleError(f'{branch.name} has no location to sell from.')
    customer = virtual_location(company.pk, Location.CUSTOMER)
    manual_max = manual_weight_limit(company.pk)
    wanted = sorted({item['product'] for item in data['items']})
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=wanted, company=company)}
    if len(products) != len(wanted):
        raise OfflineSaleError('A product on this sale was not found.')

    lines, exceptions = [], []
    for item in data['items']:
        product, label, label_problem = products[item['product']], None, ''
        if item.get('barcode'):
            try:
                label = read_label(company, item['barcode'], day)
            except serializers.ValidationError as error:
                label_problem = '; '.join(str(message) for message in error.detail)
            else:
                product = label.product if label.product.pk in products else product
        weight = label.weight_kg if label is not None else item.get('weight_kg')
        if product.is_weighed and weight is None:
            raise OfflineSaleError(f'{product.name}: the kg sold is missing.')
        quantity = item.get('quantity') or 1
        try:
            rate = rate_for(product.vat_code, day)
        except NoVatRate:
            raise OfflineSaleError(f'{product.name}: no VAT rate for "{product.vat_code}" on {day:%d/%m/%Y}.') from None
        lines.append((product, quantity, weight, label, label_problem, item, rate))

    cashier = _company_user(company, data.get('cashier'))
    if cashier is None:
        cashier = user
        exceptions.append((None, SaleException.CASHIER, {'detail': 'The cashier on this sale was not found; '
                                                                   'recorded under who sent it.'}))
    total = sum((item['charged'] for item in data['items']), Decimal('0.00'))
    cash = data['payment_method'] == Sale.CASH
    tendered = data.get('amount_tendered') if cash else total
    tendered = total if tendered is None else tendered
    sale = Sale.objects.create(
        session=session, cashier=cashier, payment_method=data['payment_method'], total=total,
        amount_tendered=tendered, change_due=max(tendered - total, Decimal('0.00')) if cash else Decimal('0.00'),
        client_ref=data['client_ref'], loyalty_barcode=data.get('loyalty_barcode', ''),
        offline_number=data.get('offline_number', ''), sold_at=sold_at, card_auth_code=data.get('card_auth_code', ''),
    )
    if not session.is_open:
        exceptions.append((None, SaleException.SESSION_CLOSED, {'detail': 'The session was closed before this sale '
                                                                          'was sent; its cash-up does not include it.'}))

    for product, quantity, weight, label, label_problem, item, rate in lines:
        charged = item['charged']
        typed = weight is not None and label is None and not item.get('barcode')
        net, vat = split_inclusive(charged, rate)
        line = SaleItem.objects.create(
            sale=sale, product=product, product_name=product.name, quantity=quantity,
            unit_price=(product.price_per_kg.quantize(CENTS, ROUND_HALF_UP) if weight is not None
                        else (charged if item.get('barcode') else product.price)),
            weight_kg=weight, price_per_kg=product.price_per_kg if weight is not None else None,
            line_total=charged, vat_code=product.vat_code, vat_rate=rate, net_amount=net, vat_amount=vat,
            barcode=item.get('barcode', '')[:64], label_price=label.label_price if label else None,
            manual_weight=typed,
        )
        if label_problem:
            exceptions.append((line, SaleException.LABEL, {'product': product, 'detail': label_problem[:255]}))
        else:
            expected = _expected(product, quantity, weight, label)
            if expected != charged:
                exceptions.append((line, SaleException.PRICE, {'product': product, 'expected': expected,
                                                               'charged': charged}))
        if typed and weight > manual_max:
            exceptions.append((line, SaleException.WEIGHT_LIMIT, {
                'product': product, 'weight_kg': weight,
                'detail': f'{weight} kg typed; the limit is {manual_max.normalize():f} kg.'}))
        allocations, short_units, short_kg = _take(product, quantity, weight, locations, customer, day, sale)
        for batch, taken, kg in allocations:
            SaleAllocation.objects.create(item=line, batch=batch, quantity=taken, weight_kg=kg)
        if short_units or short_kg:
            exceptions.append((line, SaleException.STOCK, {
                'product': product, 'quantity': short_units or None, 'weight_kg': short_kg or None,
                'detail': 'Sold offline without enough stock here; booked from adjustment.'}))

    for line, kind, fields in exceptions:
        SaleException.objects.create(sale=sale, item=line, kind=kind, **fields)

    void = data.get('void')
    if void:
        voider = _company_user(company, void.get('voided_by')) or user
        reverse_sale(sale, user=voider, reason=void.get('reason', ''), voided_at=void.get('voided_at'),
                     notify_loyalty=False)
    elif sale.loyalty_barcode:
        connection = active_connection(company.pk)
        if connection:
            emit(connection, 'loyalty.award', {'barcode': sale.loyalty_barcode, 'amount': str(total),
                                               'order_reference': _loyalty_reference(sale)})
    return sale, True, sorted({kind for _, kind, _ in exceptions})


def _short(product, quantity, weight, locations, customer, day, sale):
    return take_short(product, quantity=quantity, weight_kg=weight, location=locations[0], destination=customer,
                      reference=sale.receipt_number, expiry=day)


def _take(product, quantity, weight, locations, customer, day, sale):
    """Take a line's stock FEFO (in date on the sale ``day``); returns ``(allocations, units short, kg short)``."""
    if not product.is_weighed:
        allocations, short = take_fefo(product, quantity, locations=locations, destination=customer, today=day)
        allocations = [(batch, taken, None) for batch, taken in allocations]
        if short:
            allocations.append(_short(product, short, None, locations, customer, day, sale))
        return allocations, short, Decimal('0')
    batches = list(StockBatch.objects.filter(product=product, location__in=locations, expiration_date__gte=day))
    if not product.is_catch_weight:
        held = sum((b.weight_kg for b in batches if b.weight_kg > 0), Decimal('0.000'))
        if held <= 0:
            return [_short(product, 0, weight, locations, customer, day, sale)], 0, weight
        # take_weighed books a gap from ADJUSTMENT into the last batch itself; it is reported the same way.
        allocations = take_weighed(product, quantity=0, weight_kg=weight, locations=locations, destination=customer,
                                   today=day)
        return allocations, 0, max(weight - held, Decimal('0.000'))
    pieces = sum(b.quantity for b in batches if b.quantity > 0)
    if pieces >= quantity:
        return take_weighed(product, quantity=quantity, weight_kg=weight, locations=locations, destination=customer,
                            today=day), 0, Decimal('0')
    allocations, kg_taken = [], Decimal('0.000')
    if pieces:
        kg_taken = (weight * pieces / quantity).quantize(KG, ROUND_HALF_UP)
        try:
            allocations = take_weighed(product, quantity=pieces, weight_kg=kg_taken, locations=locations,
                                       destination=customer, today=day)
        except LedgerError:
            allocations, kg_taken, pieces = [], Decimal('0.000'), 0
    allocations.append(_short(product, quantity - pieces, weight - kg_taken, locations, customer, day, sale))
    return allocations, quantity - pieces, weight - kg_taken
