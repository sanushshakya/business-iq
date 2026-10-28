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
    admin, and never exposed through the API. ``push_prices`` adds each linked product's price to its
    ``stock.changed`` snapshot (Phase 3).
    """

    company = models.OneToOneField('tenants.Company', on_delete=models.CASCADE, related_name='storefront_connection')
    name = models.CharField(max_length=60, default='M18 storefront')
    base_url = models.URLField(blank=True, validators=[validate_base_url])
    webhook_secret = EncryptedTextField()
    is_active = models.BooleanField(default=True)
    push_prices = models.BooleanField(
        default=False,
        help_text='Send IQ prices with the stock updates; the storefront then takes its prices for linked products '
                  'from IQ only. Check the nightly report for price differences before switching on.',
    )
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


class StockSyncState(models.Model):
    """
    Whether a storefront-linked product's online stock needs sending (``pending``), and what was sent last.

    ``version`` goes up by one with every send, so the storefront can ignore a snapshot older than the one it has.
    """

    product = models.OneToOneField('inventory.Product', on_delete=models.CASCADE, related_name='stock_sync')
    pending = models.BooleanField(default=True)
    version = models.PositiveBigIntegerField(default=0)
    last_sent_available = models.IntegerField(null=True, blank=True)
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=['pending'])]

    def __str__(self):
        return f"{self.product} v{self.version}{' (pending)' if self.pending else ''}"


class StockReconciliation(models.Model):
    """
    One nightly comparison of IQ's online stock with what the storefront holds. ``mismatches`` lists
    ``{product_id, kind, iq, m18}`` where kind is ``different``, ``missing_in_m18`` or ``not_linked_in_iq``.
    Every linked product is resent after the run, so differences are fixed; this row is the report.
    """

    connection = models.ForeignKey(StorefrontConnection, on_delete=models.CASCADE, related_name='reconciliations')
    ran_at = models.DateTimeField(auto_now_add=True)
    checked = models.PositiveIntegerField(default=0)
    mismatches = models.JSONField(default=list)
    error = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-ran_at', '-pk']

    def __str__(self):
        return f"Stock check {self.ran_at:%Y-%m-%d %H:%M}: {len(self.mismatches)} mismatch(es)"
