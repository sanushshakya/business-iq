# common/models.py

from django.db import models

class DemandAlert(models.Model):
    """
    Model representing a demand alert for a product in a specific branch.

    Fields:
    - event_name: CharField representing the name of the event.
    - product_name: CharField representing the name of the product.
    - days_until_event: IntegerField representing the number of days until the event.
    - recommended_order_quantity: IntegerField representing the recommended order quantity.
    - dismissed: BooleanField indicating whether the alert has been dismissed.
    """

    event_name = models.CharField(max_length=255)
    product_name = models.CharField(max_length=255)
    days_until_event = models.IntegerField()
    recommended_order_quantity = models.IntegerField()
    dismissed = models.BooleanField(default=False)

    def __str__(self):
        return f"DemandAlert for {self.product_name} until {self.days_until_event} days"