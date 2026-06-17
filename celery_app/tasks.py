# celery_app/tasks.py

import datetime
from django.utils import timezone
from celery import shared_task
from common.models import Product
from .celery import app

@app.task(bind=True)
def apply_decay_pricing(self):
    """
    Celery task to apply decay pricing to products daily.

    This task iterates through all products, calculates the remaining shelf life,
    and applies a decay factor to the price based on the shelf life.
    """
    # Get the current date and time
    now = timezone.now()

    # Calculate the threshold for applying decay (e.g., 30 days before expiration)
    threshold_days = 30

    # Fetch products with remaining shelf life greater than the threshold
    products_to_decay = Product.objects.filter(
        expires_at__gt=now,
        expires_at__lt=now + datetime.timedelta(days=threshold_days),
    )

    for product in products_to_decay:
        # Calculate the remaining days until expiration
        remaining_days = (product.expires_at - now).days

        # Define a decay factor based on the remaining shelf life
        if remaining_days < 10:
            decay_factor = 0.25  # 25% off for less than 10 days left
        elif remaining_days < 20:
            decay_factor = 0.5   # 50% off for less than 20 days left
        else:
            decay_factor = 0.75  # 25% discount for up to 30 days left

        # Calculate the new price after applying the decay factor
        new_price = product.price * (1 - decay_factor)

        # Update the product's price and log the change
        product.price = new_price
        product.save()
        product.price_change_log.create(
            old_price=product.price,
            new_price=new_price,
            changed_by=self.request.user if hasattr(self, 'request') else None
        )

    return f"Decay pricing applied to {len(products_to_decay)} products."
```

This Celery task `apply_decay_pricing` iterates through products with a remaining shelf life greater than 30 days but less than 100 days. It calculates the decay factor based on the remaining days and applies it to the product's price. The new price is then saved, and a change log entry is created to record the price change. This task can be scheduled using Celery Beat to run daily at a specific time.