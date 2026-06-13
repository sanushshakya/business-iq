# config/celery.py

import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

@app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    """
    Schedule periodic tasks using Celery Beat.
    """
    # Add a new periodic task to run the scan_demand_alerts task every Monday at 06:00 UTC
    sender.add_periodic_task(
        crontab(hour=6, minute=0, day_of_week=0),  # Crontab for Monday at 06:00 UTC
        scan_demand_alerts.s(),  # Task to run
        name='scan demand alerts weekly'  # Name of the task
    )

@app.task
def check_low_stock():
    """
    Celery task to check for products with low stock levels and create alerts if necessary.
    """
    from authentication.models import Product, Branch, StockAlert
    from datetime import datetime

    threshold = 5  # Example threshold value

    for product in Product.objects.all():
        current_qty = sum(batch.quantity_remaining for batch in product.batches.filter(branch=branch))
        if current_qty < product.reorder_threshold:
            StockAlert.objects.create(
                product=product,
                branch=branch,
                current_qty=current_qty,
                threshold=product.reorder_threshold,
                created_at=datetime.now(),
                is_dismissed=False
            )

@app.task
def scan_demand_alerts():
    """
    Celery task to scan for demand alerts based on specific criteria.
    """
    from common.models import DemandAlert

    # Logic to scan demand alerts can be added here
    pass