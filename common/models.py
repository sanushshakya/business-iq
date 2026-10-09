# common/models.py

from django.db import models
from django.db.models import Q


class BaseModel(models.Model):
    """Abstract base: creation and last-modified timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class DemandAlert(models.Model):
    """
    A request from a branch for more stock of a product.

    ``scan_demand_alerts`` marks alerts as handled once they have been open for 24 hours.
    """

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='demand_alerts')
    product = models.CharField(max_length=255)
    branch = models.CharField(max_length=255)
    requested_qty = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_handled = models.BooleanField(default=False)
    # Set when ``scan_demand_alerts`` raised this alert for an upcoming calendar event.
    event = models.ForeignKey(
        'demand_calendar.Event', null=True, blank=True, on_delete=models.SET_NULL, related_name='stock_up_alerts'
    )
    cultural_event = models.ForeignKey(
        'demand.CulturalEvent', null=True, blank=True, on_delete=models.SET_NULL, related_name='stock_up_alerts'
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'product', 'event'],
                condition=Q(event__isnull=False),
                name='one_demand_alert_per_product_and_event',
            ),
            models.UniqueConstraint(
                fields=['company', 'product', 'cultural_event'],
                condition=Q(cultural_event__isnull=False),
                name='one_demand_alert_per_product_and_cultural_event',
            ),
        ]

    def __str__(self):
        return f"{self.product} in {self.branch} - {self.requested_qty} units requested"


class StockAlert(models.Model):
    """
    Raised by ``check_low_stock`` when a product falls below its reorder threshold.
    """

    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE, related_name='stock_alerts')
    current_qty = models.IntegerField()
    threshold = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_dismissed = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.product.name}: {self.current_qty} left (threshold {self.threshold})"


class FreightRateCache(models.Model):
    """
    The baseline rate for a company's shipping service, which ``check_freight_rates`` compares new rates with.

    It is recorded the first time a service is seen and replaced whenever an alert is raised.
    """

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='freight_rates')
    service_code = models.CharField(max_length=100, help_text="Shipping service or lane, e.g. 'CN-UK-SEA'.")
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='GBP')
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'service_code'], name='one_freight_rate_per_service')]

    def __str__(self):
        return f"{self.service_code}: {self.rate} {self.currency}"


class Setting(BaseModel):
    """A named, per-company configuration value (e.g. the default margin percent)."""

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='settings')
    key = models.CharField(max_length=100)
    value = models.TextField()
    description = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'key'], name='one_setting_per_key')]
        ordering = ['key']

    def __str__(self):
        return self.key
