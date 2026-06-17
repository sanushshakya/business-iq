# common/services/price_recommendation_service.py

import logging
from datetime import datetime, timedelta
from django.utils import timezone
from .models import StockBatch, PriceChangeLog

class PriceRecommendationService:
    """
    Service class to compute recommended retail prices based on landed cost and margin percent.

    This service can be used throughout the application to ensure that all price computations are handled consistently.
    """

    def __init__(self, logger_name='PriceRecommendationService'):
        # Initialize a logger for logging price changes
        self.logger = logging.getLogger(logger_name)

    def calculate_recommended_price(self, landed_cost, margin_percent):
        """
        Calculate the recommended retail price based on the landed cost and margin percent.

        Args:
            landed_cost (float): The total cost of the product including all expenses.
            margin_percent (float): The desired profit margin as a percentage.

        Returns:
            float: The calculated recommended retail price.
        """
        if landed_cost <= 0 or margin_percent <= 0:
            raise ValueError("Landed cost and margin percent must be greater than zero.")

        # Calculate the recommended retail price
        recommended_price = landed_cost * (1 + margin_percent / 100)

        # Log the price change
        self.logger.info(f"Calculated recommended price: {recommended_price} based on landed cost: {landed_cost} and margin percent: {margin_percent}")

        return recommended_price

    def apply_markdown_discounts(self, stock_batch):
        """
        Apply markdown discounts to a StockBatch based on shelf life remaining percentage.

        Args:
            stock_batch (StockBatch): The StockBatch object for which to apply the discount.
        """
        # Calculate the percentage of shelf life remaining
        current_date = timezone.now()
        shelf_life_remaining = (stock_batch.expiry_date - current_date).days / (stock_batch.expiry_date - stock_batch.created_at).days * 100

        if shelf_life_remaining < 25:
            discount_percentage = 20
        elif shelf_life_remaining < 50:
            discount_percentage = 15
        else:
            discount_percentage = 10

        # Calculate the discounted price
        original_price = stock_batch.product.price
        discounted_price = original_price * (1 - discount_percentage / 100)

        # Update the product price and log the change
        stock_batch.product.price = discounted_price
        stock_batch.product.save()
        self.logger.info(f"Applied markdown discount of {discount_percentage}% to product: {stock_batch.product.name}, new price: {discounted_price}")

    def create_price_change_log_entry(self, stock_batch, reason):
        """
        Create a PriceChangeLog entry for the given StockBatch with the specified reason.

        Args:
            stock_batch (StockBatch): The StockBatch object associated with the change.
            reason (str): The reason for the price change.
        """
        # Create and save a new PriceChangeLog entry
        PriceChangeLog.objects.create(
            stock_batch=stock_batch,
            old_price=stock_batch.product.price,
            new_price=stock_batch.product.price,
            reason=reason,
            changed_at=timezone.now(),
        )
        self.logger.info(f"Created price change log entry for product: {stock_batch.product.name}, reason: {reason}")

    def query_stock_batches_for_decay_pricing(self):
        """
        Query StockBatches for decay pricing — where expiry_date is not null and quantity_remaining > 0.

        Returns:
            queryset: A queryset of StockBatch objects that are eligible for decay pricing.
        """
        return StockBatch.objects.filter(expiry_date__isnull=False, quantity_remaining__gt=0)

    def apply_decay_pricing(self):
        """
        Apply decay pricing to all applicable StockBatches and log the changes.
        """
        stock_batches = self.query_stock_batches_for_decay_pricing()
        for batch in stock_batches:
            original_price = batch.product.price
            self.apply_markdown_discounts(batch)
            new_price = batch.product.price
            if original_price != new_price:
                self.create_price_change_log_entry(batch, reason='decay_markdown')