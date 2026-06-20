# logistics/models.py

from django.db import models
from common.models import ShopifyConnection

class UserSupplier(models.Model):
    """
    Model representing a user supplier.

    Fields:
    - company: ForeignKey to the Company model, linking the connection to a specific company.
    - name: CharField representing the name of the supplier.
    - country_of_origin: CharField representing the country where the supplier is based.
    - product_categories: JSONField representing the categories of products supplied by the supplier.
    - lead_time_days: IntegerField representing the estimated time in days for delivery from this supplier.
    - notes: TextField representing additional information or notes about the supplier.
    """
    company = models.ForeignKey(ShopifyConnection, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    country_of_origin = models.CharField(max_length=100)
    product_categories = models.JSONField()
    lead_time_days = models.IntegerField()
    notes = models.TextField(blank=True, null=True)

class AlternativeSupplier(models.Model):
    """
    Model representing an alternative supplier.

    Fields:
    - company: ForeignKey to the Company model, linking the connection to a specific company.
    - name: CharField representing the name of the supplier.
    - country_of_origin: CharField representing the country where the supplier is based.
    - product_categories: JSONField representing the categories of products supplied by the supplier.
    - lead_time_days: IntegerField representing the estimated time in days for delivery from this supplier.
    """
    company = models.ForeignKey(ShopifyConnection, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    country_of_origin = models.CharField(max_length=100)
    product_categories = models.JSONField()
    lead_time_days = models.IntegerField()

class FreightAlert(models.Model):
    """
    Model representing a freight alert.

    Fields:
    - user_supplier: ForeignKey to the UserSupplier model, linking the alert to a specific supplier.
    - status: CharField representing the current status of the freight (e.g., 'pending', 'shipped').
    - tracking_number: CharField representing the tracking number for the shipment.
    """
    user_supplier = models.ForeignKey(UserSupplier, on_delete=models.CASCADE)
    status = models.CharField(max_length=50)
    tracking_number = models.CharField(max_length=100)
