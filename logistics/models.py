# logistics/models.py

from django.db import models
from authentication.models import Company

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
```

This file defines the `FreightAlert` model with the specified fields. Each field is properly documented to explain its purpose and characteristics. The model uses a foreign key to link back to the `Company` model from the `authentication` app, ensuring that each freight alert is associated with a specific company.