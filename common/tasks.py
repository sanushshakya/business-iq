# common/tasks.py

import math
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from demand.models import CulturalEvent, EventProductKeyword
from demand_calendar.models import Event
from inventory.models import Product
from inventory.projections import average_daily_demand

from .models import DemandAlert, StockAlert
from .services.settings_service import get_company_setting


@shared_task
def check_low_stock():
    """
    Create a StockAlert for every product at or below its reorder threshold that has no open alert.

    Returns the number of alerts created.
    """
    low = Product.objects.filter(stock_quantity__lte=F('reorder_threshold')).exclude(
        stock_alerts__is_dismissed=False
    )
    created = 0
    for product in low:
        StockAlert.objects.create(
            product=product, current_qty=product.stock_quantity, threshold=product.reorder_threshold
        )
        created += 1
    return created


DEFAULT_CULTURAL_EVENT_MULTIPLIER = Decimal('1.5')
ALL_BRANCHES = 'All branches'


def _units_needed(product, multiplier, days):
    """
    Extra units to buy so that ``product`` covers ``days`` days at ``multiplier`` times its usual demand.

    Without any sales history the reorder threshold stands in for the usual demand of the period.
    """
    usual = average_daily_demand(product) * days or Decimal(product.reorder_threshold)
    return max(math.ceil(usual * multiplier - product.stock_quantity), 0)


def _raise_alert(product, quantity, **link):
    """Create the alert unless this product already has one for this event; returns 1 if created."""
    if quantity <= 0:
        return 0
    _, created = DemandAlert.objects.get_or_create(
        company=product.company, product=product.name, **link,
        defaults={'branch': ALL_BRANCHES, 'requested_qty': quantity},
    )
    return int(created)


@shared_task
def scan_demand_alerts(lead_days=None):
    """
    Raise "stock up" alerts for events starting within ``lead_days`` (default ``DEMAND_ALERT_LEAD_DAYS``).

    * Calendar events (``demand_calendar.Event``): every product in one of the event's categories, scaled by the
      event's ``demand_multiplier`` over the event's length.
    * Cultural events (``demand.CulturalEvent``): the products linked to them through ``EventProductKeyword``,
      scaled by the company's ``cultural_event_multiplier`` setting (default 1.5).

    An alert is only raised when current stock will not cover the extra demand, and never twice for the same
    product and event, so running this repeatedly is safe. Returns the number of alerts created.
    """
    today = timezone.localdate()
    window_end = today + timedelta(days=lead_days or settings.DEMAND_ALERT_LEAD_DAYS)
    created = 0

    for event in Event.objects.filter(end_date__gte=today, start_date__lte=window_end).prefetch_related('product_categories'):
        categories = [c.pk for c in event.product_categories.all()]
        if not categories:
            continue  # nothing says which products this event affects
        length = (event.end_date - event.start_date).days + 1
        for product in Product.objects.filter(category_id__in=categories).select_related('company'):
            created += _raise_alert(product, _units_needed(product, event.demand_multiplier, length), event=event)

    cultural = CulturalEvent.objects.filter(end_date__date__gte=today, start_date__date__lte=window_end)
    for event in cultural:
        length = (event.end_date.date() - event.start_date.date()).days + 1
        for link in EventProductKeyword.objects.filter(cultural_event=event).select_related('product__company'):
            product = link.product
            multiplier = get_company_setting(product.company_id, 'cultural_event_multiplier', DEFAULT_CULTURAL_EVENT_MULTIPLIER, Decimal)
            created += _raise_alert(product, _units_needed(product, multiplier, length), cultural_event=event)
    return created
