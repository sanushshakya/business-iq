# config/sync/models.py

"""
Module for Django models in the sync app of the iq project.
"""

import re

from django.core.validators import RegexValidator
from django.db import models

from common.fields import EncryptedTextField

class SyncTask(models.Model):
    """
    Model representing a synchronization task.
    
    Attributes:
        task_id (str): Unique identifier for the task.
        source_system (str): Name of the source system.
        target_system (str): Name of the target system.
        status (str): Current status of the task (e.g., 'pending', 'running', 'completed').
        start_time (datetime): Timestamp when the task started.
        end_time (datetime): Timestamp when the task ended.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='sync_tasks')
    task_id = models.CharField(max_length=100, unique=True)
    source_system = models.CharField(max_length=100)
    target_system = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"SyncTask {self.task_id} from {self.source_system} to {self.target_system} - Status: {self.status}"


# Shopify store domains look like "my-store.myshopify.com". This is also what stops a tenant pointing the
# server (and the access token it sends) at an arbitrary host.
SHOP_DOMAIN_PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]*\.myshopify\.com$')


class ShopifyConnection(models.Model):
    """
    Credentials for a company's Shopify store.

    ``access_token`` is encrypted at rest (see ``common.fields.EncryptedTextField``).
    """

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='shopify_connections')
    shop_domain = models.CharField(
        max_length=255,
        unique=True,
        validators=[RegexValidator(SHOP_DOMAIN_PATTERN, "Enter a Shopify domain like 'my-store.myshopify.com'.")],
    )
    access_token = EncryptedTextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.shop_domain
