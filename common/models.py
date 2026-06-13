# common/models.py

from django.db import models

class DemandAlert(models.Model):
    """
    Model representing a demand alert for a product in a specific branch.

    Fields:
    - product: Foreign key to the product that requires attention.
    - branch: Foreign key to the branch where the product is needed.
    - requested_qty: Integer field representing the quantity requested.
    - created_at: DateTimeField indicating when the alert was created.
    - is_handled: Boolean field indicating whether the alert has been addressed.

    Methods:
    - __str__: String representation of the model.
    """
    
    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE)
    branch = models.ForeignKey('inventory.Branch', on_delete=models.CASCADE)
    requested_qty = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_handled = models.BooleanField(default=False)

    def __str__(self):
        return f"DemandAlert for {self.product} at {self.branch} (requested: {self.requested_qty})"