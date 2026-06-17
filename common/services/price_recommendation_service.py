# common/services/price_recommendation_service.py

import logging
from datetime import datetime, timedelta
from django.utils import timezone
from .models import StockBatch

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

    def apply_decay_pricing_to_batches(self):
        """
        Apply decay pricing to all eligible StockBatches.
        """
        # Query the database for eligible StockBatches
        eligible_batches = self.query_stock_batches_for_decay_pricing()
        
        # Apply markdown discounts to each eligible batch
        for batch in eligible_batches:
            self.apply_markdown_discounts(batch)

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
    - quantity_remaining: IntegerField representing the remaining quantity of the batch.
    """
    product = models.ForeignKey('common.Product', on_delete=models.CASCADE)
    batch_number = models.CharField(max_length=255, unique=True)
    expiry_date = models.DateTimeField()
    quantity_remaining = models.IntegerField()

# common/tasks.py

from celery import shared_task
from .services.price_recommendation_service import PriceRecommendationService

@shared_task
def apply_decay_pricing_task():
    """
    Celery task to apply decay pricing to eligible StockBatches.
    """
    price_recommender = PriceRecommendationService()
    price_recommender.apply_decay_pricing_to_batches()

# common/serializers.py

from rest_framework import serializers
from .models import Product, StockBatch, PriceChangeLog

class ProductSerializer(serializers.ModelSerializer):
    """
    Serializer for the Product model.

    This serializer is used to convert Product objects into JSON and vice versa.
    """

    class Meta:
        model = Product
        fields = '__all__'

class StockBatchSerializer(serializers.ModelSerializer):
    """
    Serializer for the StockBatch model.

    This serializer is used to convert StockBatch objects into JSON and vice versa.
    """

    class Meta:
        model = StockBatch
        fields = '__all__'