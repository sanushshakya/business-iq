# common/models.py

from django.db import models


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
