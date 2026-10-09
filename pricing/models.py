"""
pricing/models.py

This file contains Django models for the pricing application.
"""

from django.conf import settings
import uuid

from django.db import models

def invoice_upload_path(instance, filename):
    """Store invoices as ``invoices/<company>/<random>.<ext>``: the client's file name is never used."""
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'bin'
    return f"invoices/{instance.company_id}/{uuid.uuid4().hex}.{extension}"


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

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    plan = models.ForeignKey(PricingPlan, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user} - {self.plan.name}"

class SupplierInvoice(models.Model):
    """
    Model representing an invoice from a supplier.
    
    Fields:
    - invoice_number: CharField representing the unique identifier of the invoice.
    - company: the tenant company that owns (received) the invoice.
    - supplier: ForeignKey to inventory.Supplier, the supplier that issued it. Protected from deletion while it has invoices.
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

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='supplier_invoices')
    invoice_number = models.CharField(max_length=50)
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.PROTECT, related_name='invoices')
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    issued_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    payment_status = models.CharField(max_length=10, choices=INVOICE_STATUS_CHOICES, default='pending')
    file = models.FileField(upload_to=invoice_upload_path, blank=True, help_text='The scanned or PDF invoice (PDF, PNG or JPEG).')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['company', 'invoice_number'], name='unique_invoice_number_per_company'),
        ]

    def __str__(self):
        return self.invoice_number

class InvoiceLineItem(models.Model):
    """
    Model representing an individual line item in an invoice.
    
    Fields:
    - invoice: ForeignKey to the SupplierInvoice model, representing the invoice this line item belongs to.
    - description: CharField representing a brief description of the item.
    - quantity: IntegerField representing the number of units of the item.
    - price_per_unit: DecimalField representing the price per unit of the item.
    - total_price: DecimalField representing the total price for this line item.
    """

    INVOICE_LINE_ITEM_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
    ]

    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE)
    description = models.CharField(max_length=255)
    quantity = models.IntegerField()
    price_per_unit = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=INVOICE_LINE_ITEM_STATUS_CHOICES, default='pending')

    def __str__(self):
        return f"{self.invoice.invoice_number} - {self.description}"


class PriceChangeLog(models.Model):
    """
    Record of a change to a product's price.

    Approved, unprocessed logs are pushed to Shopify by ``pricing.tasks.sync_approved_prices``.
    """

    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE, related_name='price_changes')
    stock_batch = models.ForeignKey(
        'inventory.StockBatch', null=True, blank=True, on_delete=models.SET_NULL, related_name='price_changes'
    )
    old_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=100, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    is_approved = models.BooleanField(default=False)
    is_processed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.product.name}: {self.old_price} -> {self.new_price}"
