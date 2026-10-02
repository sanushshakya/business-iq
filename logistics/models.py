# logistics/models.py

from django.db import models
from tenants.models import Company

class FreightAlert(models.Model):
    """
    Model representing a freight alert for a shipping lane.

    Fields:
    - company: ForeignKey to the Company model, linking the alert to a specific company.
    - shipping_lane: CharField representing the shipping lane associated with the alert.
    - current_rate: DecimalField representing the current rate for the shipping lane.
    - baseline_rate: DecimalField representing the baseline rate for comparison.
    - change_percent: FloatField representing the percentage change in rates.
    - alert_date: DateTimeField representing the date the alert was generated.
    - is_dismissed: BooleanField indicating whether the alert has been dismissed by the user.
    """
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    shipping_lane = models.CharField(max_length=255)
    current_rate = models.DecimalField(max_digits=10, decimal_places=2)
    baseline_rate = models.DecimalField(max_digits=10, decimal_places=2)
    change_percent = models.FloatField()
    alert_date = models.DateTimeField(auto_now_add=True)
    is_dismissed = models.BooleanField(default=False)

    def __str__(self):
        return f"Freight Alert for {self.company.name} on {self.shipping_lane}"


class LogisticProvider(models.Model):
    """
    Represents a logistic provider in the system.

    Attributes:
        name (str): The name of the logistic provider.
        address (str): The address of the logistic provider.
        phone_number (str): The phone number of the logistic provider.
    """

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='logistic_providers')
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
