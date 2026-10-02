# celery_app/tasks.py

import logging
from django.conf import settings
import time
from celery import shared_task
from .models import PriceChangeLog

logger = logging.getLogger(__name__)

@shared_task
def sync_approved_prices():
    """
    Celery task to process the queue of approved PriceChangeLogs and update product prices on Shopify.
    """
    # Fetch all approved PriceChangeLogs from the database
    approved_logs = PriceChangeLog.objects.filter(is_approved=True)

    for log in approved_logs:
        try:
            # Get the associated Shopify connection
            shopify_conn = log.company.shopifyconnection_set.first()
            
            if not shopify_conn:
                logger.error(f"No Shopify connection found for company {log.company.id}")
                continue

            # Update product price on Shopify
            shopify_conn.update_product_price(log.product_id, log.new_price)
            log.is_processed = True
            log.save()

        except Exception as e:
            logger.exception(f"Failed to sync approved price for log {log.id}: {e}")

    logger.info("Finished syncing all approved prices.")


@shared_task(idempotent=True)
def process_sync_event(event_id):
    """
    Celery task to process events idempotently.
    
    Args:
        event_id (int): The unique identifier for the event to process.

    Returns:
        bool: True if the event was processed successfully, False otherwise.
    """
    try:
        # Simulate event processing logic
        time.sleep(2)  # Sleep to mimic time-consuming operation
        print(f"Processing event {event_id}")
        return True
    except Exception as e:
        print(f"Failed to process event {event_id}: {e}")
        return False
