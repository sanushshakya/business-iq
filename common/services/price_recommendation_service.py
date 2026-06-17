# common/services/price_recommendation_service.py

import logging
from datetime import datetime, timedelta
from django.utils import timezone

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

    def query_stock_batches_for_decay_pricing(self):
        """
        Query StockBatches for decay pricing — where expiry_date is not null and quantity_remaining > 0.

        Returns:
            queryset: A queryset of StockBatch objects that are eligible for decay pricing.
        """
        # Calculate the current date
        now = timezone.now()
        
        # Define a buffer period before expiration to consider batches for potential decay
        buffer_days = 7
        
        # Query the database for StockBatches where expiry_date is not null, quantity_remaining > 0,
        # and where the batch is within the buffer period of expiration
        eligible_batches = StockBatch.objects.filter(
            expiry_date__isnull=False,
            quantity_remaining__gt=0,
            expiry_date__lte=now + timedelta(days=buffer_days),
        )
        
        return eligible_batches

# common/models.py

from django.db import models
from .services.price_recommendation_service import PriceRecommendationService

class StockBatch(models.Model):
    """
    Model representing a batch of stock with details including expiration date and remaining quantity.

    Fields:
    - product: ForeignKey to the Product model, representing the product in this batch.
    - batch_number: CharField representing the unique batch number for tracking.
    - expiry_date: DateTimeField representing the expiration date of the batch.
    - quantity_remaining: IntegerField representing the quantity of the product remaining in this batch.
    """

    product = models.ForeignKey('common.Product', on_delete=models.CASCADE)
    batch_number = models.CharField(max_length=50, unique=True)
    expiry_date = models.DateTimeField()
    quantity_remaining = models.IntegerField()

    def apply_decay_pricing(self):
        """
        Apply decay pricing to the batch based on the remaining shelf life.

        This method calculates the recommended price considering the time left until expiration.
        """
        if self.expiry_date is not None and self.quantity_remaining > 0:
            # Calculate days remaining
            days_remaining = (self.expiry_date - timezone.now()).days

            # Define a decay rate — here, a simple linear decrease in price over time
            decay_rate_per_day = 1.0 / 365.0  # Assuming annual decay rate of 1%

            # Calculate the new recommended price
            new_price = self.product.price * (1 - days_remaining * decay_rate_per_day)

            # Ensure the new price is not below zero
            if new_price < 0:
                new_price = 0

            # Update the product's price with the decayed value
            self.product.price = new_price
            self.product.save()

            # Log the change
            PriceRecommendationService().logger.info(f"Applied decay pricing to {self.batch_number}: New price is {new_price}")