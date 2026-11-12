"""
Drafting purchase orders (G57): for each product sold by the unit with a preferred supplier, enough to last until the
delivery after next — the supplier's lead time plus how often we order from them — at the projected demand (average
daily sales × the demand events on each day) plus the product's reorder threshold as
safety stock, less what is on hand and on order, in whole packs. One draft per supplier; under the supplier's minimum
the lines that run out soonest are topped up (never past twice their target), else the draft is flagged. Nothing is
sent. A draft nobody touched is rewritten by the next run (keeping its number); a supplier with an edited draft gets no
new one, and edited and approved orders count as on order. Demand events are calendar events (by category) and
cultural events (by linked product, the company's ``cultural_event_multiplier``), as the stock-up alerts use.
"""

import logging
import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from celery import shared_task
from django.db import transaction
from django.db.models import Max, Q

from common.services.settings_service import get_company_setting
from demand.models import EventProductKeyword
from demand_calendar.models import Event
from inventory import projections
from inventory.models import Product
from tenants.models import Company
from vat.services import uk_date

from .models import PurchaseOrder, PurchaseOrderLine, SupplierProduct

logger = logging.getLogger(__name__)
ZERO = Decimal('0.00')
ONE = Decimal('1')
DEFAULT_CULTURAL_EVENT_MULTIPLIER = Decimal('1.5')  # as the stock-up alerts (common.tasks)


def _on_order(company, today):
    """Units per product on edited drafts (whatever their date: they are waiting for approval) and approved orders
    still to arrive."""
    lines = PurchaseOrderLine.objects.filter(order__company=company).filter(
        Q(order__status=PurchaseOrder.APPROVED, order__expected_on__gte=today)
        | Q(order__status=PurchaseOrder.DRAFT, order__edited=True))
    units = defaultdict(int)
    for product_id, packs, pack_size in lines.values_list('product_id', 'packs', 'pack_size'):
        units[product_id] += packs * pack_size
    return units


def _orderable(company):
    """Products we draft for: sold by the unit (catch-weight by pieces), not loose by weight."""
    return Product.objects.filter(company=company).filter(Q(sold_by=Product.EACH) | Q(is_catch_weight=True))


class _Events:
    """The demand events in the run's window, loaded once: calendar events by product category (their multiplier)
    and cultural events by linked product (the company's ``cultural_event_multiplier``), as the stock-up alerts."""

    def __init__(self, company, today, days):
        end = today + timedelta(days=days)
        self.calendar = [(event.name, event.start_date, event.end_date, event.demand_multiplier,
                          {category.pk for category in event.product_categories.all()})
                         for event in Event.objects.filter(start_date__lte=end, end_date__gte=today)
                         .prefetch_related('product_categories')]
        multiplier = get_company_setting(company.pk, 'cultural_event_multiplier', DEFAULT_CULTURAL_EVENT_MULTIPLIER,
                                         Decimal)
        self.cultural = defaultdict(list)
        for link in EventProductKeyword.objects.filter(
                product__company=company, cultural_event__start_date__date__lte=end,
                cultural_event__end_date__date__gte=today).select_related('cultural_event'):
            event = link.cultural_event
            self.cultural[link.product_id].append((event.name, event.start_date.date(), event.end_date.date(),
                                                   multiplier))

    def on(self, product, day):
        """The events raising ``product``'s demand on ``day``: [(name, multiplier)]."""
        found = [(name, rate) for name, start, end, rate, categories in self.calendar
                 if start <= day <= end and product.category_id in categories]
        found += [(name, rate) for name, start, end, rate in self.cultural.get(product.pk, []) if start <= day <= end]
        return [(name, rate) for name, rate in found if rate > ONE]


def _line(link, today, on_order, events):
    product, supplier = link.product, link.supplier
    horizon = supplier.lead_time_days + supplier.order_cycle_days
    base = projections.average_daily_demand(product)
    demand, names, stockout = Decimal('0'), set(), None
    covered = product.stock_quantity + on_order
    for offset in range(horizon):
        day = today + timedelta(days=offset)
        raising = events.on(product, day)
        names.update(name for name, _rate in raising)
        demand += base * max((rate for _name, rate in raising), default=ONE)
        if base > 0 and stockout is None and demand >= covered:
            stockout = day
    target = math.ceil(demand) + product.reorder_threshold
    need = target - covered
    if need <= 0:
        return None
    return PurchaseOrderLine(
        product=product, packs=math.ceil(need / link.pack_size), pack_size=link.pack_size, pack_cost=link.pack_cost,
        daily_demand=base.quantize(Decimal('0.01')), horizon_days=horizon, target=target,
        on_hand=product.stock_quantity, on_order=on_order, events=', '.join(sorted(names))[:255],
        stockout_date=stockout)


def _cost(line):
    line.line_cost = (line.pack_cost or ZERO) * line.packs
    return line.line_cost


def _top_up(lines, minimum):
    """Add whole packs, soonest to run out first, until ``minimum`` is met, never leaving more than twice a line's
    target in stock and on order. Returns whether it is met."""
    total = sum(_cost(line) for line in lines)
    for line in sorted(lines, key=lambda line: (line.stockout_date or date.max, line.product.name)):
        while (total < minimum and line.pack_cost
               and line.on_hand + line.on_order + (line.packs + 1) * line.pack_size <= 2 * line.target):
            line.packs += 1
            line.topped_up += 1
            total += line.pack_cost
    for line in lines:
        _cost(line)
    return total >= minimum


def _next_number(company):
    return (PurchaseOrder.objects.filter(company=company).aggregate(last=Max('number'))['last'] or 0) + 1


def _save_draft(company, supplier, lines, today, user, draft):
    """Write ``lines`` into ``draft`` (an untouched one, kept with its number) or a new draft; no lines: the
    untouched draft goes. Returns the order or None."""
    if not lines:
        if draft is not None:  # nothing needed now: cancelled, not deleted, so its number is never used again
            draft.status = PurchaseOrder.CANCELLED
            draft.save(update_fields=['status'])
        return None
    met = not supplier.min_order_value or _top_up(lines, supplier.min_order_value)
    if draft is None:
        draft = PurchaseOrder(company=company, number=_next_number(company), supplier=supplier)
    else:
        draft.lines.all().delete()  # an untouched draft is rewritten whole, keeping its number
    draft.expected_on = today + timedelta(days=supplier.lead_time_days)
    draft.below_minimum, draft.created_by = not met, user
    draft.total_cost = sum((_cost(line) for line in lines), ZERO)
    draft.save()
    for line in lines:
        line.order = draft
    PurchaseOrderLine.objects.bulk_create(lines)
    return draft


@transaction.atomic
def draft_orders(company, today=None, user=None):
    """Draft one order per supplier for ``company``. Returns ``{created, replaced, kept, skipped}`` (``kept``: suppliers
    with an edited draft, which get no new one; ``skipped``: products with suppliers but none preferred)."""
    today = today or uk_date()
    # One run at a time per company; drafts are re-read under the lock, so one approved or edited meanwhile is kept.
    Company.objects.select_for_update().filter(pk=company.pk).first()
    on_order = _on_order(company, today)
    orderable = _orderable(company)
    drafts = PurchaseOrder.objects.select_for_update().filter(company=company, status=PurchaseOrder.DRAFT)
    edited = set(drafts.filter(edited=True).values_list('supplier_id', flat=True))
    untouched = {order.supplier_id: order for order in drafts.filter(auto=True, edited=False).order_by('number')}
    links = (SupplierProduct.objects.filter(supplier__company=company, preferred=True, product__in=orderable)
             .exclude(supplier_id__in=edited).select_related('product', 'supplier')
             .order_by('supplier_id', 'product__name'))
    events = _Events(company, today, max((link.supplier.lead_time_days + link.supplier.order_cycle_days
                                          for link in links), default=1))
    by_supplier, suppliers = defaultdict(list), {}
    for link in links:
        suppliers[link.supplier_id] = link.supplier
        line = _line(link, today, on_order.get(link.product_id, 0), events)
        if line is not None:
            by_supplier[link.supplier_id].append(line)
    summary = {'created': 0, 'replaced': 0, 'kept': len(edited),
               'skipped': orderable.filter(supplier_links__isnull=False).exclude(supplier_links__preferred=True)
               .distinct().count()}
    for supplier_id in sorted(set(by_supplier) | set(untouched)):
        draft = untouched.get(supplier_id)
        supplier = suppliers.get(supplier_id) or draft.supplier
        if supplier_id in edited:  # an edited draft stands for this supplier; an untouched one beside it goes
            draft.status = PurchaseOrder.CANCELLED
            draft.save(update_fields=['status'])
            continue
        order = _save_draft(company, supplier, by_supplier.get(supplier_id, []), today, user, draft)
        if order is not None:
            summary['replaced' if draft is not None else 'created'] += 1
    return summary


@shared_task
def run_nightly():
    """Every night (beat): drafts for every company; one company's problem never stops the others."""
    for company in Company.objects.order_by('pk'):
        try:
            draft_orders(company)
        except Exception:  # noqa: BLE001
            logger.exception('Drafting purchase orders failed for company %s', company.pk)
