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