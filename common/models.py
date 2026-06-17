# common/models.py

from django.db import models

class Product(models.Model):
    """
    Model representing a product with details including a commodity code.

    Fields:
    - name: CharField representing the name of the product.
    - description: TextField representing the detailed description of the product.
    - price: DecimalField representing the current price of the product.
    - created_at: DateTimeField representing the creation timestamp of the record.
    - updated_at: DateTimeField representing the last update timestamp of the record.
    """
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class PriceChangeLog(models.Model):
    """
    Model representing a log entry for price changes of products.

    Fields:
    - product: ForeignKey to the Product model, linking the change to a specific product.
    - old_price: DecimalField storing the previous price before the change.
    - new_price: DecimalField storing the new price after the change.
    - changed_at: DateTimeField representing the timestamp when the price was changed.
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    old_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
    changed_at = models.DateTimeField(auto_now_add=True)

# common/serializers.py

from rest_framework import serializers
from .models import StockProjection, PriceChangeLog

class StockProjectionSerializer(serializers.ModelSerializer):
    """
    Serializer for the StockProjection model.

    This serializer is used to convert StockProjection objects into JSON and vice versa.
    """

class PriceChangeLogSerializer(serializers.ModelSerializer):
    """
    Serializer for the PriceChangeLog model.

    This serializer is used to convert PriceChangeLog objects into JSON and vice versa.
    """

    class Meta:
        model = PriceChangeLog
        fields = '__all__'