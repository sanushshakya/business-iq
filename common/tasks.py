# common/tasks.py

from datetime import timedelta

from celery import shared_task
from django.db.models import F
from django.utils import timezone

from inventory.models import Product

from .models import DemandAlert, StockAlert


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


@shared_task
def scan_demand_alerts(max_age_hours=24):
    """
    Mark demand alerts that have been open longer than ``max_age_hours`` as handled.

    Returns the number of alerts updated.
    """
    cutoff = timezone.now() - timedelta(hours=max_age_hours)
    return DemandAlert.objects.filter(is_handled=False, created_at__lt=cutoff).update(is_handled=True)
