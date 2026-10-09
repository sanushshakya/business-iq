# inventory/projections.py

"""
Demand estimates and stock projections for a product.

Demand is the average daily quantity sent out (outbound ``StockMovement``s) over the last
``DEMAND_HISTORY_DAYS`` days. Calendar events (``demand_calendar.Event``) scale it up or down on the days
they cover for products in the event's categories.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from demand_calendar.models import Event

from .models import StockMovement

ONE = Decimal(1)
TWO_PLACES = Decimal('0.01')


def average_daily_demand(product, days: Optional[int] = None, now=None) -> Decimal:
    """Units sold per day, averaged over the last ``days`` days (0 when there is no sales history)."""
    days = days or settings.DEMAND_HISTORY_DAYS
    since = (now or timezone.now()) - timedelta(days=days)
    sold = StockMovement.objects.filter(
        batch__product=product, movement_type='outbound', date_time__gte=since
    ).aggregate(total=Sum('quantity'))['total'] or 0
    return Decimal(sold) / Decimal(days)


def _events_for(product, start, end):
    if product.category_id is None:
        return []
    return list(Event.objects.filter(end_date__gte=start, start_date__lte=end, product_categories=product.category_id))


def project_stock(product, days: int = 30, today=None) -> dict:
    """
    Project the product's stock for the next ``days`` days from its current quantity.

    Returns the projection per day plus when stock is expected to run out or reach the reorder threshold.
    """
    today = today or timezone.localdate()
    base = average_daily_demand(product)
    events = _events_for(product, today, today + timedelta(days=days))

    stock = Decimal(product.stock_quantity)
    rows, stockout_date, reorder_date = [], None, None
    for offset in range(days):
        day = today + timedelta(days=offset)
        multiplier = max((e.demand_multiplier for e in events if e.start_date <= day <= e.end_date), default=ONE)
        demand = base * multiplier
        stock = max(stock - demand, Decimal(0))
        if base > 0 and stockout_date is None and stock <= 0:
            stockout_date = day
        if reorder_date is None and stock <= product.reorder_threshold:
            reorder_date = day
        rows.append({
            'date': day,
            'projected_quantity': stock.quantize(TWO_PLACES),
            'expected_demand': demand.quantize(TWO_PLACES),
            'demand_multiplier': multiplier,
        })

    return {
        'product_id': product.pk,
        'product_name': product.name,
        'current_stock': product.stock_quantity,
        'reorder_threshold': product.reorder_threshold,
        'average_daily_demand': base.quantize(TWO_PLACES),
        'days_of_cover': (Decimal(product.stock_quantity) / base).quantize(TWO_PLACES) if base > 0 else None,
        'stockout_date': stockout_date,
        'reorder_date': reorder_date,
        'projection': rows,
    }
