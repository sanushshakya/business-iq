from django.db import models

# Module-level docstring
"""
This module contains the Django models for the logistics app.
"""

class LogisticProvider(models.Model):
    """
    Represents a logistic provider in the system.

    Attributes:
        name (str): The name of the logistic provider.
        address (str): The address of the logistic provider.
        phone_number (str): The phone number of the logistic provider.
    """

    name = models.CharField(max_length=255)
    address = models.TextField()
    phone_number = models.CharField(max_length=15)

    def __str__(self):
        return self.name

class Delivery(models.Model):
    """
    Represents a delivery order in the system.

    Attributes:
        provider (LogisticProvider): The logistic provider handling this delivery.
        shipment_date (datetime.date): The date when the shipment is expected to be delivered.
        status (str): The current status of the delivery ('Pending', 'In Transit', 'Delivered').
    """

    provider = models.ForeignKey(LogisticProvider, on_delete=models.CASCADE)
    shipment_date = models.DateField()
    status = models.CharField(max_length=20, choices=[('Pending', 'Pending'), ('In Transit', 'In Transit'), ('Delivered', 'Delivered')], default='Pending')

    def __str__(self):
        return f"Delivery for {self.provider.name} on {self.shipment_date}"