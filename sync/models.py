# config/sync/models.py

"""
Module for Django models in the sync app of the iq project.
"""

import re
import uuid
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from common.fields import EncryptedTextField

def new_task_id():
    return uuid.uuid4().hex


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
    task_id = models.CharField(max_length=100, unique=True, default=new_task_id)
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

    class Meta:
        constraints = [
            # One store per company: products carry a single Shopify id and the price sync needs to know which store.
            models.UniqueConstraint(fields=['company'], name='one_shopify_connection_per_company'),
        ]

    def __str__(self):
        return self.shop_domain


def validate_base_url(value):
    """http(s) only, and no credentials in the URL (it is used for outgoing requests)."""
    try:
        parts = urlsplit(value)
    except ValueError:
        raise ValidationError('Enter a valid URL.')
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValidationError('Enter an http(s) URL.')
    if parts.username is not None or parts.password is not None:
        raise ValidationError('The URL must not contain credentials.')


class StorefrontConnection(models.Model):
    """
    A company's link to the m18 storefront.

    ``webhook_secret`` signs webhooks in both directions. It is encrypted at rest, set only in the Django
    admin, and never exposed through the API.
    """

    company = models.OneToOneField('tenants.Company', on_delete=models.CASCADE, related_name='storefront_connection')
    name = models.CharField(max_length=60, default='M18 storefront')
    base_url = models.URLField(blank=True, validators=[validate_base_url])
    webhook_secret = EncryptedTextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class InboundEvent(models.Model):
    """A webhook event received from the storefront; unique per ``event_id`` so redelivery is a no-op."""

    STATUS_CHOICES = [
        ('applied', 'Applied'),
        ('partial', 'Partial'),
        ('unmatched', 'Unmatched'),
    ]

    connection = models.ForeignKey(StorefrontConnection, on_delete=models.CASCADE, related_name='events')
    event_id = models.UUIDField()
    event_type = models.CharField(max_length=40)
    payload = models.JSONField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    detail = models.JSONField(default=list)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['connection', 'event_id'], name='unique_inbound_event_per_connection'),
        ]

    def __str__(self):
        return f"{self.event_type} {self.event_id}"


class OutboundEvent(models.Model):
    """
    A webhook event for the storefront (transactional outbox): written in the same transaction as the change it
    reports, sent as soon as that commits, and retried by the sweep until delivered or failed.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('delivered', 'Delivered'),
        ('failed', 'Failed'),
    ]

    connection = models.ForeignKey(StorefrontConnection, on_delete=models.CASCADE, related_name='outbound_events')
    event_id = models.UUIDField(default=uuid.uuid4, unique=True)
    event_type = models.CharField(max_length=40)
    payload = models.JSONField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'next_attempt_at'])]

    def __str__(self):
        return f"{self.event_type} {self.event_id} ({self.status})"
