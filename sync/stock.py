# sync/stock.py

"""
Online stock for the m18 storefront.

A product linked to the storefront (``external_id`` set, company connected) is offered online with its in-date
batch stock at active, online-sellable locations (the same ones web orders take from) minus ``online_buffer``
("keep for shop"), never below zero. Any stock change marks the product's
``StockSyncState`` pending; ``sync.tasks.push_storefront_stock`` sends the pending ones every 15 s.
"""

import logging

from django.db import DatabaseError, transaction
from django.db.models import Sum
from django.utils import timezone

from inventory.ledger import ONLINE_LOCATION
from inventory.models import Product, StockBatch

from .models import StockSyncState, StorefrontConnection

logger = logging.getLogger(__name__)


def online_available(products):
    """``{product pk: units offered online}`` for ``products``."""
    today = timezone.localdate()
    totals = dict(
        StockBatch.objects.filter(
            product__in=[p.pk for p in products], quantity__gt=0, expiration_date__gte=today,
            **{f'location__{field}': value for field, value in ONLINE_LOCATION.items()},
        )
        .values('product')
        .annotate(total=Sum('quantity'))
        .values_list('product', 'total')
    )
    return {p.pk: max((totals.get(p.pk) or 0) - p.online_buffer, 0) for p in products}


def linked_products():
    """Products whose company has an active storefront connection with somewhere to send to."""
    connected = StorefrontConnection.objects.filter(is_active=True).exclude(base_url='').values('company_id')
    return Product.objects.exclude(external_id='').filter(company_id__in=connected)


def _upsert_pending(product_ids):
    now = timezone.now()
    StockSyncState.objects.bulk_create(
        [StockSyncState(product_id=pk, pending=True, changed_at=now) for pk in product_ids],
        update_conflicts=True, unique_fields=['product'], update_fields=['pending', 'changed_at'],
    )


def mark_location_changed(location_id):
    """A location's flags may have changed: queue every linked product with stock there (same rules as below)."""
    try:
        with transaction.atomic():
            ids = list(
                linked_products().filter(stockbatch__location_id=location_id).values_list('pk', flat=True).distinct()
            )
            if ids:
                _upsert_pending(ids)
    except DatabaseError:
        logger.exception('Could not mark products at location %s for a storefront stock update', location_id)


def mark_changed(product_id):
    """
    Queue a linked product's online stock to be sent. Runs inside the caller's transaction (so it rolls back
    with the change) and never fails the caller's write: a database error here is logged and swallowed.
    """
    try:
        with transaction.atomic():  # a savepoint, so a failure here leaves the caller's transaction usable
            if linked_products().filter(pk=product_id).exists():
                _upsert_pending([product_id])
    except DatabaseError:
        logger.exception('Could not mark product %s for a storefront stock update', product_id)


def mark_all(company_id=None):
    """Queue every linked product, or one company's (first rollout, and the nightly resend). Returns how many."""
    products = linked_products()
    if company_id is not None:
        products = products.filter(company_id=company_id)
    ids = list(products.values_list('pk', flat=True))
    for start in range(0, len(ids), 1000):
        _upsert_pending(ids[start:start + 1000])
    return len(ids)
