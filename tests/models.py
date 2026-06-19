# tests/models.py

from django.db import models
from django.core.validators import URLValidator
from common.models import ShopifyConnection

class StockMovement(models.Model):
    """
    Model representing a stock movement event.

    Fields:
    - shop_connection: ForeignKey to the ShopifyConnection model, linking the stock movement to a specific Shopify store.
    - product_id: CharField representing the unique identifier of the product involved in the movement.
    - quantity_change: IntegerField representing the change in quantity (positive for addition, negative for removal).
    - timestamp: DateTimeField representing the time when the stock movement occurred.
    """
    shop_connection = models.ForeignKey(ShopifyConnection, on_delete=models.CASCADE)
    product_id = models.CharField(max_length=255)
    quantity_change = models.IntegerField()
    timestamp = models.DateTimeField(auto_now_add=True)

class StockLevel(models.Model):
    """
    Model representing the current stock level of a product.

    Fields:
    - shop_connection: ForeignKey to the ShopifyConnection model, linking the stock level to a specific Shopify store.
    - product_id: CharField representing the unique identifier of the product.
    - current_stock: IntegerField representing the current stock quantity.
    """
    shop_connection = models.ForeignKey(ShopifyConnection, on_delete=models.CASCADE)
    product_id = models.CharField(max_length=255)
    current_stock = models.IntegerField()
```

This file contains two models for testing purposes. `StockMovement` represents an event where the stock of a product changes, and `StockLevel` represents the current stock level of a product at a given time. Both models are linked to a `ShopifyConnection` to ensure that the stock movements and levels are associated with specific Shopify stores.