# authentication/signals.py

from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Invoice
from common.models import PriceChangeLog

@receiver(post_save, sender=Invoice)
def create_price_change_log(sender, instance, created, **kwargs):
    """
    Signal handler to create a new PriceChangeLog record when an invoice is marked as processed.
    
    Args:
    - sender: The model class for which the signal was sent.
    - instance: The instance of the model that was saved or updated.
    - created: A boolean indicating whether this save operation created a new record.
    - **kwargs: Additional keyword arguments passed by the dispatcher.

    Returns:
    None
    """
    if not created and instance.is_processed:
        # Retrieve the product associated with the invoice
        product = instance.product
        
        # Check if there is a previous price change log entry for this product
        previous_price_log = PriceChangeLog.objects.filter(product=product).order_by('-created_at').first()
        
        # If there is a previous price log, compare the current price to the previous one
        if previous_price_log and instance.amount != previous_price_log.new_price:
            # Create a new PriceChangeLog record with the old and new prices
            PriceChangeLog.objects.create(
                product=product,
                old_price=previous_price_log.new_price,
                new_price=instance.amount
            )