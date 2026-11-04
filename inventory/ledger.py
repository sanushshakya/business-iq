# inventory/ledger.py

"""
Stock ledger primitives: the only code that changes stock.

Every batch sits at a location and every ``StockMovement`` has a source and a destination. A movement changes one
batch: inbound adds to it (its location is the destination), outbound takes from it (its location is the source);
the other side is the ``counterpart`` — a virtual location (SUPPLIER, CUSTOMER, WASTE, ADJUSTMENT, IN-TRANSIT) or,
for transfers, another real one. So stock at a location is what came in minus what went out.

``move`` appends a movement and applies it; ``take_fefo`` takes units from batches at the given locations,
earliest expiry first. ``transfer_batch`` and ``transfer_fefo`` move units between two locations into the same lot
(transfers: source → IN-TRANSIT → destination).

Products sold by weight also move kg (``weight_kg``): ``take_weighed`` takes kg FEFO for loose products and
pieces FEFO (with their kg) for catch-weight ones. Recorded kg drifts from real kg, so a gap between what is sold and
what the record holds is booked to (or from) ADJUSTMENT; no batch goes below 0 pieces or 0 kg.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import F
from django.utils import timezone

from .models import Location, Product, StockBatch, StockMovement


# What makes a location count online: web orders take from it and online stock counts it (one definition for both).
ONLINE_LOCATION = {'online_sellable': True, 'is_active': True}


KG = Decimal('0.001')


class LedgerError(Exception):
    """A movement that would break the ledger's rules (e.g. a location from another company)."""

    def __init__(self, message, *, available=None):
        super().__init__(message)
        self.available = available  # for a shortfall: how many units there were


def virtual_location(company, type_):
    """The company's virtual location of ``type_`` (``supplier``, ``customer``, ``waste``, ...)."""
    company_id = getattr(company, 'pk', company)
    return Location.objects.get(company_id=company_id, type=type_)


def sellable_locations(branch):
    """Where a till in ``branch`` may sell from."""
    return Location.objects.filter(branch=branch, sellable=True, is_active=True).order_by('pk')


def online_locations(company):
    """Where online orders are taken from; online stock (``sync.stock.online_available``) counts the same places."""
    return Location.objects.filter(company=company, **ONLINE_LOCATION).order_by('pk')


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


def move(batch, quantity, movement_type, *, counterpart, weight_kg=None, transfer=None, breakdown=None,
         write_off=None):
    """
    Append a ledger entry, apply it to the batch and the product, and return it (the batch row must be locked).
    Products sold by weight need ``weight_kg`` (kg moved, may be 0 for a count-only entry); each products never send it.
    """
    location = batch.location
    product = Product.objects.filter(pk=batch.product_id).values('company_id', 'sold_by').first()
    if location.company_id != product['company_id']:
        raise LedgerError("A batch's location must belong to its product's company.")
    if counterpart.company_id != location.company_id:
        raise LedgerError('Stock can only move between locations of the same company.')
    weighed = product['sold_by'] == Product.WEIGHT
    if weighed and (weight_kg is None or weight_kg < 0):
        raise LedgerError('A product sold by weight moves in kg.')
    if not weighed and weight_kg is not None:
        raise LedgerError('Only products sold by weight move in kg.')
    sign = 1 if movement_type == 'inbound' else -1
    if movement_type == 'inbound':
        source, destination = counterpart, location
    else:
        source, destination = location, counterpart
    # Exact values from the locked row (an F() sum of kg drifts where the database stores floats); never below 0.
    pieces, kg = StockBatch.objects.filter(pk=batch.pk).values_list('quantity', 'weight_kg').get()
    changes = {'quantity': pieces + sign * quantity}
    if weighed:
        changes['weight_kg'] = kg + sign * weight_kg
    if changes['quantity'] < 0 or changes.get('weight_kg', 0) < 0:
        raise LedgerError(f'Batch {batch.batch_number} holds {pieces} unit(s) and {kg} kg; that would go below 0.')
    StockBatch.objects.filter(pk=batch.pk).update(**changes)
    Product.objects.filter(pk=batch.product_id).update(stock_quantity=F('stock_quantity') + sign * quantity)
    return StockMovement.objects.create(
        batch=batch, quantity=quantity, weight_kg=weight_kg, movement_type=movement_type,
        source_location=source, destination_location=destination, transfer=transfer, breakdown=breakdown,
        write_off=write_off,
    )


def take_fefo(product, quantity, *, locations, destination, today=None):
    """
    Take up to ``quantity`` units from the product's in-date batches at ``locations``, earliest expiry first
    (ties: oldest receipt). Batches elsewhere are never touched.

    Must be called inside ``transaction.atomic()`` (the batch rows are locked). Writes one outbound movement per
    batch used, to ``destination``, and returns ``(allocations, shortfall)``: ``allocations`` is a list of
    ``(batch, taken)`` and ``shortfall`` is how many units could not be covered. Each products only (weighed ones
    use ``take_weighed``).
    """
    if product.is_weighed:
        raise LedgerError(f'{product} is sold by weight; take it by kg.')
    allocations, remaining = [], quantity
    for batch in _fefo_batches(product, locations, today):
        if remaining <= 0:
            break
        take = min(batch.quantity, remaining)
        move(batch, take, 'outbound', counterpart=destination)
        allocations.append((batch, take))
        remaining -= take
    return allocations, max(remaining, 0)


def take_weighed(product, *, quantity, weight_kg, locations, destination, today=None):
    """
    Take a weighed product from its in-date batches at ``locations`` to ``destination``; returns
    ``[(batch, pieces, kg), ...]``. Inside ``transaction.atomic()`` (the batches are locked).

    - Loose (kg only): ``weight_kg`` FEFO by kg; ``quantity`` is not deducted. If the in-date kg is short, the gap is
      booked from ADJUSTMENT into the last batch used first. No in-date kg at all is a ``LedgerError``.
    - Catch-weight: ``quantity`` pieces FEFO (too few is a ``LedgerError`` with ``available``). The line's
      ``weight_kg`` is split over the batches by pieces (3 dp, remainder on the last); a batch holding less kg than
      its share gets the gap from ADJUSTMENT, and a batch whose last piece goes sends any kg left to ADJUSTMENT.
      ``weight_kg=None`` (kg unknown, e.g. a web order) takes each batch's average kg per piece.
    """
    adjustment = virtual_location(product.company_id, Location.ADJUSTMENT)
    if not product.is_catch_weight:
        batches = list(_fefo_batches(product, locations, today, by='weight_kg'))
        if not batches:
            raise LedgerError(f'No {product} in date here.', available=Decimal('0'))
        allocations, remaining = [], weight_kg
        for index, batch in enumerate(batches):
            if remaining <= 0:
                break
            held = batch.weight_kg
            if index == len(batches) - 1 and held < remaining:
                move(batch, 0, 'inbound', counterpart=adjustment, weight_kg=remaining - held)
                held = remaining
            take = min(held, remaining)
            move(batch, 0, 'outbound', counterpart=destination, weight_kg=take)
            allocations.append((batch, 0, take))
            remaining -= take
        return allocations

    batches = list(_fefo_batches(product, locations, today))
    available = sum(batch.quantity for batch in batches)
    if available < quantity:
        raise LedgerError(f'Only {available} of {product} in date here.', available=available)
    allocations, pieces_left, kg_taken = [], quantity, Decimal('0')
    for batch in batches:
        if pieces_left <= 0:
            break
        pieces = min(batch.quantity, pieces_left)
        last_of_batch = pieces == batch.quantity
        if weight_kg is None:
            kg = batch.weight_kg if last_of_batch else _kg(batch.weight_kg * pieces / batch.quantity)
        else:  # cumulative rounding: the shares sum to the line's kg exactly and are never negative
            kg = _kg(weight_kg * (quantity - pieces_left + pieces) / quantity) - kg_taken
        held = batch.weight_kg
        if held < kg:
            move(batch, 0, 'inbound', counterpart=adjustment, weight_kg=kg - held)
            held = kg
        move(batch, pieces, 'outbound', counterpart=destination, weight_kg=kg)
        if last_of_batch and held > kg:
            move(batch, 0, 'outbound', counterpart=adjustment, weight_kg=held - kg)
        allocations.append((batch, pieces, kg))
        pieces_left -= pieces
        kg_taken += kg
    return allocations


def _kg(value):
    return value.quantize(KG, rounding=ROUND_HALF_UP)


def _fefo_batches(product, locations, today=None, by='quantity'):
    """
    The product's in-date batches holding stock (``by``: pieces, or ``weight_kg`` for loose products) at
    ``locations``, in FEFO order, locked (inside a transaction).
    """
    today = today or timezone.localdate()
    return (
        StockBatch.objects.select_for_update(of=('self',))  # lock the batches, not the joined locations
        .select_related('location')
        .filter(product=product, location__in=locations, expiration_date__gte=today, **{f'{by}__gt': 0})
        .order_by('expiration_date', 'receive_date', 'pk')
    )


def transfer_batch(batch, quantity, to, *, transfer=None):
    """
    Move ``quantity`` units of a (locked) batch to location ``to``, into the same lot there (same batch number and
    expiry; created empty if missing): an outbound movement from the batch and an inbound one into the lot at ``to``.
    A lot with that number but another expiry at ``to`` is a ``LedgerError`` (never merge two expiries). Returns the batch at ``to``. Must run inside ``transaction.atomic()``.
    """
    held, _ = StockBatch.objects.get_or_create(
        product_id=batch.product_id, batch_number=batch.batch_number, location=to,
        defaults={'quantity': 0, 'expiration_date': batch.expiration_date, 'supplier_id': batch.supplier_id},
    )
    held = StockBatch.objects.select_for_update(of=('self',)).select_related('location').get(pk=held.pk)
    if held.expiration_date != batch.expiration_date:
        raise LedgerError(
            f'Batch {batch.batch_number} at {to.code} has another expiry ({held.expiration_date:%d %b %Y}, not '
            f'{batch.expiration_date:%d %b %Y}); correct the lot number first.'
        )
    move(batch, quantity, 'outbound', counterpart=to, transfer=transfer)
    move(held, quantity, 'inbound', counterpart=batch.location, transfer=transfer)
    return held


def transfer_fefo(product, quantity, *, source, to, transfer=None, today=None):
    """
    Move ``quantity`` units of ``product`` from its in-date batches at ``source`` to ``to`` (earliest expiry first,
    each into the same lot at ``to``). Returns ``[(source batch, batch at to, taken), ...]``. If ``source`` has fewer
    in-date units, raises ``LedgerError`` (with ``available``) before moving anything. Inside ``transaction.atomic()``.
    """
    batches = list(_fefo_batches(product, [source], today))
    available = sum(batch.quantity for batch in batches)
    if available < quantity:
        raise LedgerError(f'Only {available} unit(s) of {product} available at {source.code}.', available=available)
    allocations, remaining = [], quantity
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity, remaining)
        allocations.append((batch, transfer_batch(batch, take, to, transfer=transfer), take))
        remaining -= take
    return allocations
