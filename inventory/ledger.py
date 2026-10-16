# inventory/ledger.py

"""
Stock ledger primitives shared by the till and the storefront webhook.

``move`` appends a ``StockMovement`` and applies it to the batch and the product. ``take_fefo`` takes units
from a product's batches, earliest expiry first.
"""

from django.db.models import F
from django.utils import timezone

from .models import Product, StockBatch, StockMovement


def move(batch, quantity, movement_type):
    """Append a ledger entry and apply it to the batch and the product (the batch row must be locked)."""
    delta = quantity if movement_type == 'inbound' else -quantity
    StockBatch.objects.filter(pk=batch.pk).update(quantity=F('quantity') + delta)
    Product.objects.filter(pk=batch.product_id).update(stock_quantity=F('stock_quantity') + delta)
    StockMovement.objects.create(batch=batch, quantity=quantity, movement_type=movement_type)


def take_fefo(product, quantity, *, today=None):
    """
    Take up to ``quantity`` units from the product's in-date batches, oldest expiry first.

    Must be called inside ``transaction.atomic()`` (the batch rows are locked). Writes one outbound movement
    per batch used and returns ``(allocations, shortfall)``: ``allocations`` is a list of ``(batch, taken)``
    and ``shortfall`` is how many units could not be covered.
    """
    today = today or timezone.localdate()
    batches = (
        StockBatch.objects.select_for_update()
        .filter(product=product, quantity__gt=0, expiration_date__gte=today)
        .order_by('expiration_date', 'pk')
    )
    allocations, remaining = [], quantity
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity, remaining)
        move(batch, take, 'outbound')
        allocations.append((batch, take))
        remaining -= take
    return allocations, max(remaining, 0)
