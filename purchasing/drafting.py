"""
Drafting purchase orders (G57): for each product sold by the unit with a preferred supplier, enough to last until the
delivery after next — the supplier's lead time plus how often we order from them — at the projected demand (G09's
``project_stock``: average daily sales × the demand events on each day) plus the product's reorder threshold as
safety stock, less what is on hand and on order, in whole packs. One draft per supplier; under the supplier's minimum
the lines that run out soonest are topped up (never past twice their target), else the draft is flagged. Nothing is
sent. A draft nobody touched is replaced by the next run; edited and approved orders are kept and count as on order.
"""

import logging
import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from celery import shared_task
from django.db import transaction
from django.db.models import Max, Q

from demand_calendar.models import Event
from inventory.models import Product
from inventory.projections import project_stock
from tenants.models import Company
from vat.services import uk_date

from .models import PurchaseOrder, PurchaseOrderLine, SupplierProduct

logger = logging.getLogger(__name__)
ZERO = Decimal('0.00')


def _on_order(company, today):
    """Units per product on edited drafts and approved orders still to arrive."""
    lines = PurchaseOrderLine.objects.filter(order__company=company, order__expected_on__gte=today).filter(
        Q(order__status=PurchaseOrder.APPROVED) | Q(order__status=PurchaseOrder.DRAFT, order__edited=True))
    units = defaultdict(int)
    for product_id, packs, pack_size in lines.values_list('product_id', 'packs', 'pack_size'):
        units[product_id] += packs * pack_size
    return units


def _orderable(company):
    """Products we draft for: sold by the unit (catch-weight by pieces), not loose by weight."""
    return Product.objects.filter(company=company).filter(Q(sold_by=Product.EACH) | Q(is_catch_weight=True))


def _line(link, today, on_order):
    product, supplier = link.product, link.supplier
    horizon = supplier.lead_time_days + supplier.order_cycle_days
    projection = project_stock(product, days=max(horizon, 1), today=today)
    demand = sum((row['expected_demand'] for row in projection['projection'][:horizon]), Decimal('0'))
    end = today + timedelta(days=horizon - 1)
    events = sorted(Event.objects.filter(product_categories=product.category_id, start_date__lte=end,
                                         end_date__gte=today).values_list('name', flat=True)
                    ) if product.category_id else []
    target = math.ceil(demand) + product.reorder_threshold
    need = target - product.stock_quantity - on_order
    if need <= 0:
        return None
    packs = math.ceil(need / link.pack_size)
    return PurchaseOrderLine(
        product=product, packs=packs, pack_size=link.pack_size, pack_cost=link.pack_cost,
        daily_demand=projection['average_daily_demand'], horizon_days=horizon, target=target,
        on_hand=product.stock_quantity, on_order=on_order, events=', '.join(events)[:255],
        stockout_date=projection['stockout_date'])


def _cost(line):
    line.line_cost = (line.pack_cost or ZERO) * line.packs
    return line.line_cost


def _top_up(lines, minimum):
    """Add whole packs, soonest to run out first, never past twice a line's target, until ``minimum`` is met.
    Returns whether it is met."""
    total = sum(_cost(line) for line in lines)
    for line in sorted(lines, key=lambda line: (line.stockout_date or date.max, line.product.name)):
        while total < minimum and line.pack_cost and (line.packs + 1) * line.pack_size <= 2 * line.target:
            line.packs += 1
            line.topped_up += 1
            total += line.pack_cost
    for line in lines:
        _cost(line)
    return total >= minimum


@transaction.atomic
def _draft_for_supplier(company, supplier, lines, today, user, replacing):
    Company.objects.select_for_update().filter(pk=company.pk).first()  # a nightly and a manual run take turns
    # Numbered before the replaced draft goes, so a number is never used twice.
    number = (PurchaseOrder.objects.filter(company=company).aggregate(last=Max('number'))['last'] or 0) + 1
    for old in replacing:
        old.delete()
    if not lines:
        return None
    met = not supplier.min_order_value or _top_up(lines, supplier.min_order_value)
    order = PurchaseOrder.objects.create(
        company=company, number=number, supplier=supplier, expected_on=today + timedelta(days=supplier.lead_time_days),
        below_minimum=not met, created_by=user)
    for line in lines:
        line.order = order
        _cost(line)
    PurchaseOrderLine.objects.bulk_create(lines)
    order.total_cost = sum((line.line_cost for line in lines), ZERO)
    order.save(update_fields=['total_cost'])
    return order


def draft_orders(company, today=None, user=None):
    """Draft one order per supplier for ``company``. Returns ``{created, replaced, kept, skipped}`` (``skipped``:
    products with suppliers but none preferred, so not drafted)."""
    today = today or uk_date()
    on_order = _on_order(company, today)
    orderable = _orderable(company)
    links = (SupplierProduct.objects.filter(supplier__company=company, preferred=True, product__in=orderable)
             .select_related('product', 'supplier').order_by('supplier_id', 'product__name'))
    by_supplier, suppliers = defaultdict(list), {}
    for link in links:
        suppliers[link.supplier_id] = link.supplier
        line = _line(link, today, on_order.get(link.product_id, 0))
        if line is not None:
            by_supplier[link.supplier_id].append(line)
    skipped = (orderable.filter(supplier_links__isnull=False).exclude(supplier_links__preferred=True)
               .distinct().count())
    untouched = PurchaseOrder.objects.filter(company=company, status=PurchaseOrder.DRAFT, auto=True, edited=False)
    summary = {'created': 0, 'replaced': 0, 'skipped': skipped,
               'kept': PurchaseOrder.objects.filter(company=company, status=PurchaseOrder.DRAFT, edited=True).count()}
    for supplier_id in sorted(set(by_supplier) | set(untouched.values_list('supplier_id', flat=True))):
        replacing = list(untouched.filter(supplier_id=supplier_id).select_related('supplier'))
        supplier = suppliers.get(supplier_id) or replacing[0].supplier
        order = _draft_for_supplier(company, supplier, by_supplier.get(supplier_id, []), today, user, replacing)
        if order is not None:
            summary['replaced' if replacing else 'created'] += 1
    return summary


@shared_task
def run_nightly():
    """Every night (beat): drafts for every company; one company's problem never stops the others."""
    for company in Company.objects.order_by('pk'):
        try:
            draft_orders(company)
        except Exception:  # noqa: BLE001
            logger.exception('Drafting purchase orders failed for company %s', company.pk)
