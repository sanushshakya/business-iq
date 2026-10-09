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

    def calculate_recommended_price(self, landed_cost, margin_percent) -> Decimal:
        """Landed cost plus a markup, e.g. cost 10 with a 25% margin -> 12.50."""
        landed_cost, margin_percent = Decimal(str(landed_cost)), Decimal(str(margin_percent))
        if landed_cost <= 0 or margin_percent <= 0:
            raise ValueError("Landed cost and margin percent must be greater than zero.")
        recommended_price = (landed_cost * (1 + margin_percent / 100)).quantize(Decimal('0.01'))
        self.logger.info(
            "Calculated recommended price: %s (landed cost %s, margin %s%%)", recommended_price, landed_cost, margin_percent
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

    DECAY_REASON = 'decay_markdown'

    def base_price_for(self, stock_batch):
        """
        The price a batch's markdown should be calculated from.

        If a markdown for this batch has already been *approved* and nobody has changed the price since, that is
        the price before the first markdown (so deeper tiers never compound). Otherwise it is the current price.
        """
        product = stock_batch.product
        applied = list(
            PriceChangeLog.objects.filter(
                stock_batch=stock_batch, product=product, reason=self.DECAY_REASON, is_approved=True
            ).order_by('changed_at', 'pk')
        )
        if applied and applied[-1].new_price == product.price:
            return applied[0].old_price
        return product.price

    def propose_markdown(self, stock_batch):
        """
        Propose a markdown for the batch's product according to its remaining shelf life.

        Nothing about the product changes: a pending ``PriceChangeLog`` is created (or refreshed) for staff to
        approve. Returns ``(log_or_None, changed)``; ``changed`` is False when nothing new needed recording.
        """
        product = stock_batch.product
        pending = PriceChangeLog.objects.filter(stock_batch=stock_batch, reason=self.DECAY_REASON, is_approved=False)
        discount = calculate_markdown_percentage(self.shelf_life_remaining_percent(stock_batch))
        new_price = None
        if discount:
            new_price = (self.base_price_for(stock_batch) * (Decimal(100) - discount) / Decimal(100)).quantize(Decimal('0.01'))
        if new_price is None or new_price == product.price:
            return (None, bool(pending.delete()[0]))  # nothing to propose; drop any stale proposal

        log = pending.first()
        if log is None:
            log = PriceChangeLog.objects.create(
                product=product, stock_batch=stock_batch, old_price=product.price, new_price=new_price, reason=self.DECAY_REASON
            )
            self.logger.info("Proposed %s%% markdown for %s: %s -> %s", discount, product.name, product.price, new_price)
            return log, True
        if (log.old_price, log.new_price) == (product.price, new_price):
            return log, False
        log.old_price, log.new_price = product.price, new_price
        log.save(update_fields=['old_price', 'new_price'])
        return log, True

    def query_stock_batches_for_decay_pricing(self):
        """Batches that still have stock to sell."""
        return StockBatch.objects.filter(quantity__gt=0).select_related('product')

    def propose_decay_markdowns(self) -> int:
        """
        Propose markdowns for stock nearing expiry. Safe to run repeatedly (e.g. daily).

        A product's price is shared by all its batches, so only the batch closest to expiry drives it; proposals
        for its other batches are dropped. Returns how many proposals were created, changed or withdrawn.
        """
        driving = {}
        for batch in self.query_stock_batches_for_decay_pricing():
            remaining = self.shelf_life_remaining_percent(batch)
            if batch.product_id not in driving or remaining < driving[batch.product_id][0]:
                driving[batch.product_id] = (remaining, batch)

        changes = sum(self.propose_markdown(batch)[1] for _, batch in driving.values())
        stale = PriceChangeLog.objects.filter(reason=self.DECAY_REASON, is_approved=False).exclude(
            stock_batch__in=[batch for _, batch in driving.values()]
        )
        return changes + stale.delete()[0]
