# common/services/price_recommendation_service.py

import logging
from decimal import Decimal

from django.utils import timezone

from inventory.models import StockBatch
from pricing.models import PriceChangeLog


def calculate_markdown_percentage(shelf_life_remaining_percent):
    """
    Markdown (percent off) for a batch with the given percentage of shelf life remaining.

    Under 25% remaining -> 20% off, under 50% -> 15% off, otherwise no markdown.
    """
    if shelf_life_remaining_percent < 25:
        return 20
    if shelf_life_remaining_percent < 50:
        return 15
    return 0


class PriceRecommendationService:
    """
    Computes recommended retail prices and applies expiry-based markdowns.
    """

    def __init__(self, logger_name='PriceRecommendationService'):
        self.logger = logging.getLogger(logger_name)

    def calculate_recommended_price(self, landed_cost, margin_percent):
        """Landed cost plus margin, e.g. cost 10 with 25% margin -> 12.50."""
        if landed_cost <= 0 or margin_percent <= 0:
            raise ValueError("Landed cost and margin percent must be greater than zero.")
        recommended_price = landed_cost * (1 + margin_percent / 100)
        self.logger.info(
            "Calculated recommended price: %s (landed cost %s, margin %s%%)",
            recommended_price, landed_cost, margin_percent,
        )
        return recommended_price

    @staticmethod
    def shelf_life_remaining_percent(stock_batch, today=None):
        """Percentage of a batch's shelf life (receive date -> expiry) that is still left."""
        today = today or timezone.localdate()
        total_days = (stock_batch.expiration_date - stock_batch.receive_date.date()).days
        if total_days <= 0:
            return 0
        remaining_days = (stock_batch.expiration_date - today).days
        return max(remaining_days, 0) / total_days * 100

    def apply_markdown_discounts(self, stock_batch):
        """
        Mark down the batch's product according to remaining shelf life.

        Returns ``(old_price, new_price)``; both are equal when no markdown applies.
        """
        discount = calculate_markdown_percentage(self.shelf_life_remaining_percent(stock_batch))
        product = stock_batch.product
        old_price = product.price
        if discount == 0:
            return old_price, old_price

        new_price = (old_price * (Decimal(100) - discount) / Decimal(100)).quantize(Decimal('0.01'))
        product.price = new_price
        product.save(update_fields=['price'])
        self.logger.info("Applied %s%% markdown to %s: %s -> %s", discount, product.name, old_price, new_price)
        return old_price, new_price

    def create_price_change_log_entry(self, stock_batch, old_price, new_price, reason):
        return PriceChangeLog.objects.create(
            product=stock_batch.product,
            stock_batch=stock_batch,
            old_price=old_price,
            new_price=new_price,
            reason=reason,
        )

    def query_stock_batches_for_decay_pricing(self):
        """Batches that still have stock to sell."""
        return StockBatch.objects.filter(quantity__gt=0).select_related('product')

    def apply_decay_pricing(self):
        """
        Apply markdowns to all eligible batches and log each price change.

        Note: markdowns compound if this runs repeatedly while a batch stays in the same tier.
        """
        for batch in self.query_stock_batches_for_decay_pricing():
            old_price, new_price = self.apply_markdown_discounts(batch)
            if old_price != new_price:
                self.create_price_change_log_entry(batch, old_price, new_price, reason='decay_markdown')
