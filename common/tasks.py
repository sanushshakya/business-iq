# common/tasks.py
import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

from products.models import ProductBatch, StockAlert
from branches.models import Branch

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def check_low_stock(self):
    """
    Celery task to check stock levels and create alerts if the sum of quantity_remaining across batches for a branch falls below reorder_threshold.
    """
    # Get all branches
    branches = Branch.objects.all()
    
    for branch in branches:
        # Calculate the total remaining quantity for each product at this branch
        total_quantity = ProductBatch.objects.filter(branch=branch).aggregate(total_quantity=models.Sum('quantity_remaining'))['total_quantity'] or 0
        
        # Check if the total quantity is below the reorder threshold
        for product_batch in ProductBatch.objects.filter(branch=branch):
            product = product_batch.product
            if total_quantity < product.reorder_threshold:
                StockAlert.objects.create(
                    product=product,
                    branch=branch,
                    current_qty=total_quantity,
                    threshold=product.reorder_threshold,
                    created_at=timezone.now(),
                    is_dismissed=False
                )
                logger.info(f'Created stock alert for product {product.name} at branch {branch.name}')

    return 'Stock check complete'