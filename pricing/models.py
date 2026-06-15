"""
pricing/models.py

This file contains Django models for the pricing application.
"""

from django.db import models

class PricingPlan(models.Model):
    """
    Model representing a pricing plan for different services.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    price_per_month = models.DecimalField(max_digits=10, decimal_places=2)
    features = models.JSONField(default=dict)

    def __str__(self):
        return self.name

class Subscription(models.Model):
    """
    Model representing a subscription for a pricing plan.
    """

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    plan = models.ForeignKey(PricingPlan, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.username} - {self.plan.name}"

class SupplierInvoice(models.Model):
    """
    Model representing an invoice from a supplier.
    
    Fields:
    - invoice_number: CharField representing the unique identifier of the invoice.
    - supplier: ForeignKey to the Company model, representing the supplier.
    - total_amount: DecimalField representing the total amount due on the invoice.
    - issued_date: DateField representing the date when the invoice was issued.
    - due_date: DateField representing the date by which the invoice is due.
    - payment_status: CharField representing the current status of the payment (e.g., 'paid', 'pending').
    """
    
    INVOICE_STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('pending', 'Pending'),
        ('overdue', 'Overdue'),
    ]

    invoice_number = models.CharField(max_length=50, unique=True)
    supplier = models.ForeignKey('common.Company', on_delete=models.CASCADE)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    issued_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    payment_status = models.CharField(max_length=10, choices=INVOICE_STATUS_CHOICES, default='pending')

    def __str__(self):
        return self.invoice_number