# config/sync/models.py

"""
Module for Django models in the sync app of the iq project.
"""

from django.db import models

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

    task_id = models.CharField(max_length=100, unique=True)
    source_system = models.CharField(max_length=100)
    target_system = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"SyncTask {self.task_id} from {self.source_system} to {self.target_system} - Status: {self.status}"


class ShopifyConnection(models.Model):
    """
    Credentials for a company's Shopify store.

    NOTE: ``access_token`` is stored as-is; encrypt it at rest before storing real tokens.
    """

    company = models.ForeignKey('tenants.Company', on_delete=models.CASCADE, related_name='shopify_connections')
    shop_domain = models.CharField(max_length=255, unique=True)
    access_token = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.shop_domain
