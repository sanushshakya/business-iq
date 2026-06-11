# common/models.py

from django.db import models

class StockAlert(models.Model):
    """
    Model representing a stock alert for a product in a specific branch.

    Fields:
    - product: Foreign key to the product that is low on stock.
    - branch: Foreign key to the branch where the product's stock is low.
    - current_qty: Integer field representing the current quantity of the product in stock.
    - reorder_threshold: Integer field representing the reorder threshold for the product.
    - created_at: DateTimeField indicating when the alert was created.
    - is_dismissed: Boolean field indicating whether the alert has been dismissed.

    Methods:
    - __str__: String representation of the model.
    """
    
    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE)
    branch = models.ForeignKey('inventory.Branch', on_delete=models.CASCADE)
    current_qty = models.IntegerField()
    reorder_threshold = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_dismissed = models.BooleanField(default=False)

    def __str__(self):
        return f"StockAlert for {self.product} at {self.branch} (current: {self.current_qty}, threshold: {self.reorder_threshold})"