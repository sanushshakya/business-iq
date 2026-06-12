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

class CulturalEvent(models.Model):
    """
    A model representing a cultural event.

    Fields:
        name (str): The name of the cultural event.
        description (str): Description of the cultural event.
        start_date (DateTimeField): The start date and time of the event.
        end_date (DateTimeField): The end date and time of the event.
        location (str, optional): Location where the event will take place.
        created_at (DateTimeField): The timestamp when the event was created.
        updated_at (DateTimeField): The timestamp when the event was last updated.

    Methods:
        __str__: Return a string representation of the CulturalEvent instance.
    """

    name = models.CharField(max_length=255)
    description = models.TextField()
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    location = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class EventProductKeyword(models.Model):
    """
    A model representing a keyword associated with an event and product.

    Fields:
        cultural_event (ForeignKey): Foreign key to the CulturalEvent this keyword belongs to.
        product (ForeignKey): Foreign key to the product associated with the event.
        keyword (str): The keyword describing the relationship between the event and product.

    Methods:
        __str__: Return a string representation of the EventProductKeyword instance.
    """

    cultural_event = models.ForeignKey(CulturalEvent, on_delete=models.CASCADE)
    product = models.ForeignKey('common.Product', on_delete=models.CASCADE)  # Assuming Product model exists in common app
    keyword = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.cultural_event} - {self.product} - {self.keyword}"