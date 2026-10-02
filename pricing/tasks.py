# pricing/tasks.py

import logging

from celery import shared_task

from common.services.shopify_service import ShopifyService
from sync.models import ShopifyConnection

from .models import PriceChangeLog

logger = logging.getLogger(__name__)


@shared_task
def sync_approved_prices():
    """
    Push approved, unprocessed price changes to Shopify.

    Returns the number of logs processed successfully.
    """
    processed = 0
    logs = PriceChangeLog.objects.filter(is_approved=True, is_processed=False).select_related('product')
    for log in logs:
        product = log.product
        if not (product.company_id and product.shopify_product_id):
            logger.error("Price change %s: product %s has no company or Shopify id", log.id, product.id)
            continue

        connection = ShopifyConnection.objects.filter(company_id=product.company_id).first()
        if connection is None:
            logger.error("No Shopify connection found for company %s", product.company_id)
            continue

        try:
            ShopifyService(connection.shop_domain).update_product_price(product.shopify_product_id, log.new_price)
        except Exception:
            logger.exception("Failed to sync price change %s", log.id)
            continue

        log.is_processed = True
        log.save(update_fields=['is_processed'])
        processed += 1

    logger.info("Finished syncing approved prices (%s processed).", processed)
    return processed
