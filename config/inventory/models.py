"""
inventory/models.py

This module contains Django models for the inventory app. These models are responsible
for representing and managing inventory-related data within the application.
"""

from django.db import models

class Product(models.Model):
    """
    Represents a product in the inventory.

    Attributes:
        name (str): The name of the product.
        description (str): A brief description of the product.
        price (float): The current price of the product.
        stock_quantity (int): The number of units currently in stock.
    """
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock_quantity = models.IntegerField(default=0)

    def __str__(self):
        return self.name

class Order(models.Model):
    """
    Represents an order for a product.

    Attributes:
        product (Product): The product being ordered.
        quantity (int): The number of units ordered.
        ordered_at (datetime): The timestamp when the order was placed.
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.IntegerField(default=1)
    ordered_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} - Ordered {self.quantity}"

class Supplier(models.Model):
    """
    Represents a supplier for the products.

    Attributes:
        name (str): The name of the supplier.
        contact_info (str): Contact information for the supplier.
        address (str): Address of the supplier's warehouse or office.
    """
    name = models.CharField(max_length=255)
    contact_info = models.TextField()
    address = models.TextField()

    def __str__(self):
        return self.name