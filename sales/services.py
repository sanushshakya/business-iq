# sales/services.py

"""
Till operations: ringing up a sale, voiding it and cashing up a session.

A sale takes its stock from the product's batches, oldest expiry first (expired batches are never sold),
and records each take as an outbound ``StockMovement`` so demand estimates and the ledger stay correct.
A void puts the same units back with inbound movements. Everything runs in one transaction with the
affected rows locked, so two tills cannot sell the same last unit.
"""

from collections import OrderedDict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from inventory.ledger import move, take_fefo
from inventory.models import Product, StockBatch
from sync.outbox import active_connection, emit

from .models import Sale, SaleAllocation, SaleItem, TillSession

CENTS = Decimal('0.01')


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
    ``lines`` is a list of ``(Product, quantity)``. Prices come from the product, never from the client.

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

    # Merge repeated products and lock them in a fixed order so concurrent sales cannot deadlock.
    wanted = OrderedDict()
    for product, quantity in lines:
        wanted[product.pk] = (product, wanted.get(product.pk, (product, 0))[1] + quantity)
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=wanted).order_by('pk')}

    today = timezone.localdate()
    plan, total, problems = [], Decimal('0.00'), []
    for pk in sorted(wanted):
        product, quantity = wanted[pk]
        product = products[pk]
        batches = list(
            StockBatch.objects.select_for_update()
            .filter(product=product, quantity__gt=0, expiration_date__gte=today)
            .order_by('expiration_date', 'pk')
        )
        available = sum(b.quantity for b in batches)
        if quantity > available:
            problems.append(f'{product.name}: only {available} in date, {quantity} requested.')
            continue
        line_total = (product.price * quantity).quantize(CENTS)
        total += line_total
        plan.append((product, quantity, line_total))
    if problems:
        raise serializers.ValidationError({'items': problems})

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
    for product, quantity, line_total in plan:
        item = SaleItem.objects.create(
            sale=sale, product=product, product_name=product.name, quantity=quantity,
            unit_price=product.price, line_total=line_total,
        )
        allocations, shortfall = take_fefo(product, quantity, today=today)
        if shortfall:  # cannot happen after the check above (rows are locked); never oversell
            raise serializers.ValidationError({'items': [f'{product.name}: stock changed, try again.']})
        for batch, taken in allocations:
            SaleAllocation.objects.create(item=item, batch=batch, quantity=taken)
    if loyalty_barcode:
        connection = active_connection(session.till.branch.company_id)
        if connection:
            emit(connection, 'loyalty.award', {
                'barcode': loyalty_barcode, 'amount': str(total), 'order_reference': _loyalty_reference(sale),
            })
    return sale, True


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
    for allocation in allocations:
        batch = StockBatch.objects.select_for_update().get(pk=allocation.batch_id)
        move(batch, allocation.quantity, 'inbound')
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
