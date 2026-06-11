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
    sender.add_periodic_task(86400.0, check_low_stock.s(), name='check low stock daily')

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