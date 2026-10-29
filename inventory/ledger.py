# inventory/ledger.py

"""
Stock ledger primitives: the only code that changes stock.

Every batch sits at a location and every ``StockMovement`` has a source and a destination. A movement changes one
batch: inbound adds to it (its location is the destination), outbound takes from it (its location is the source);
the other side is the ``counterpart`` — a virtual location (SUPPLIER, CUSTOMER, WASTE, ADJUSTMENT, IN-TRANSIT) or,
for transfers, another real one. So stock at a location is what came in minus what went out.

``move`` appends a movement and applies it; ``take_fefo`` takes units from batches at the given locations,
earliest expiry first.
"""

from django.db.models import F
from django.utils import timezone

from .models import Location, Product, StockBatch, StockMovement


class LedgerError(Exception):
    """A movement that would break the ledger's rules (e.g. a location from another company)."""


def virtual_location(company, type_):
    """The company's virtual location of ``type_`` (``supplier``, ``customer``, ``waste``, ...)."""
    company_id = getattr(company, 'pk', company)
    return Location.objects.get(company_id=company_id, type=type_)


def sellable_locations(branch):
    """Where a till in ``branch`` may sell from."""
    return Location.objects.filter(branch=branch, sellable=True, is_active=True).order_by('pk')


def online_locations(company):
    """Where online orders are taken from (and, from G10, what online stock counts)."""
    return Location.objects.filter(company=company, online_sellable=True, is_active=True).order_by('pk')


def primary_location(company, create=False):
    """
    Where opening and legacy stock is placed: the oldest branch's active shop floor. A company without one gets
    None, or with ``create`` a sellable ``MAIN`` location (as the G09 migration did).
    """
    location = (
        Location.objects.filter(company=company, type=Location.SHOP_FLOOR, branch__isnull=False, is_active=True)
        .order_by('branch_id', 'pk').first()
    )
    if location is None and create:
        location, _ = Location.objects.get_or_create(
            company=company, code='MAIN',
            defaults={'name': 'Main stock', 'type': Location.WAREHOUSE, 'sellable': True, 'online_sellable': True},
        )
    return location


def move(batch, quantity, movement_type, *, counterpart):
    """Append a ledger entry, apply it to the batch and the product, and return it (the batch row must be locked)."""
    location = batch.location
    company_id = Product.objects.filter(pk=batch.product_id).values_list('company_id', flat=True).first()
    if location.company_id != company_id:
        raise LedgerError("A batch's location must belong to its product's company.")
    if counterpart.company_id != location.company_id:
        raise LedgerError('Stock can only move between locations of the same company.')
    if movement_type == 'inbound':
        delta, source, destination = quantity, counterpart, location
    else:
        delta, source, destination = -quantity, location, counterpart
    StockBatch.objects.filter(pk=batch.pk).update(quantity=F('quantity') + delta)
    Product.objects.filter(pk=batch.product_id).update(stock_quantity=F('stock_quantity') + delta)
    return StockMovement.objects.create(
        batch=batch, quantity=quantity, movement_type=movement_type,
        source_location=source, destination_location=destination,
    )


def take_fefo(product, quantity, *, locations, destination, today=None):
    """
    Take up to ``quantity`` units from the product's in-date batches at ``locations``, earliest expiry first
    (ties: oldest receipt). Batches elsewhere are never touched.

    Must be called inside ``transaction.atomic()`` (the batch rows are locked). Writes one outbound movement per
    batch used, to ``destination``, and returns ``(allocations, shortfall)``: ``allocations`` is a list of
    ``(batch, taken)`` and ``shortfall`` is how many units could not be covered.
    """
    today = today or timezone.localdate()
    batches = (
        StockBatch.objects.select_for_update(of=('self',))  # lock the batches, not the joined locations
        .select_related('location')
        .filter(product=product, location__in=locations, quantity__gt=0, expiration_date__gte=today)
        .order_by('expiration_date', 'receive_date', 'pk')
    )
    allocations, remaining = [], quantity
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity, remaining)
        move(batch, take, 'outbound', counterpart=destination)
        allocations.append((batch, take))
        remaining -= take
    return allocations, max(remaining, 0)
