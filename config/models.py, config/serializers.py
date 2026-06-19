# config/models.py

from django.db import models

class StockMovement(models.Model):
    """
    Model representing a movement of stock.

    Fields:
    - item_id: CharField representing the unique identifier of the stock item.
    - quantity: IntegerField representing the quantity of the stock movement.
    - movement_type: CharField representing the type of movement (e.g., 'in', 'out').
    - timestamp: DateTimeField automatically set to the current date and time when the movement is created.
    """
    item_id = models.CharField(max_length=255)
    quantity = models.IntegerField()
    movement_type = models.CharField(max_length=10, choices=[('in', 'Inbound'), ('out', 'Outbound')])
    timestamp = models.DateTimeField(auto_now_add=True)

# config/serializers.py

from rest_framework import serializers
from .models import StockMovement

class StockMovementSerializer(serializers.ModelSerializer):
    """
    Serializer for the StockMovement model.

    Meta:
    - model: StockMovement
    - fields: all fields of StockMovement
    """
    class Meta:
        model = StockMovement
        fields = '__all__'