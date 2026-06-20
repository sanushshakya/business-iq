from django.db import models

class FreightAlert(models.Model):
    """
    Model representing an alert for freight rates.

    Fields:
    - company: ForeignKey to the Company model, linking the alert to a specific company.
    - rate: DecimalField representing the current freight rate.
    - previous_rate: DecimalField representing the previous freight rate.
    - threshold: DecimalField representing the percentage change required to trigger an alert.
    - created_at: DateTimeField representing the timestamp when the alert was created.
    """
    company = models.ForeignKey('authentication.Company', on_delete=models.CASCADE)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    previous_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    threshold = models.DecimalField(max_digits=5, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Freight Alert for {self.company} - Rate: {self.rate}, Previous Rate: {self.previous_rate}"