# common/models.py

from django.db import models

class Product(models.Model):
    """
    Model representing a product with details including a commodity code.

    Fields:
    - name: CharField representing the name of the product.
    - description: TextField representing the detailed description of the product.
    - price: DecimalField representing the current price of the product.
    - created_at: DateTimeField representing the creation timestamp of the record.
    - updated_at: DateTimeField representing the last update timestamp of the record.
    - commodity_code: CharField representing the HS Tariff commodity code for the product.
    """

    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    commodity_code = models.CharField(max_length=10, blank=True, null=True)

    def __str__(self):
        return self.name

# Inline comment: Ensure the symbol is unique to avoid duplicate entries for the same stock.