# demand/models.py

from django.db import models

class Demand(models.Model):
    """
    A model representing a demand for products or services within an organization.

    Fields:
        product_name (str): The name of the product being demanded.
        quantity (int): The quantity of the product being demanded.
        description (str, optional): Additional description about the demand.
        due_date (DateField): The date by which the demand must be fulfilled.
        status (str): The current status of the demand ('Pending', 'Approved', 'Rejected').
        created_at (DateTimeField): The timestamp when the demand was created.
        updated_at (DateTimeField): The timestamp when the demand was last updated.

    Methods:
        __str__: Return a string representation of the Demand instance.
    """

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    product_name = models.CharField(max_length=255)
    quantity = models.IntegerField()
    description = models.TextField(blank=True, null=True)
    due_date = models.DateField()
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='PENDING',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.product_name
