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

Reservations (G41, ``StockReservation``) hold stock at a batch for a trade order without moving it. The FEFO takes
here use only a batch's free part (what it holds minus what is reserved); a ``move`` out of a batch that leaves it
holding less than its reservations trims them, newest first, so reserved never exceeds held.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import F, Sum
from django.utils import timezone

from .models import Location, Product, StockBatch, StockMovement, StockReservation


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
    product = Product.objects.filter(pk=batch.product_id).values('company_id', 'sold_by', 'is_catch_weight').first()
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
    if sign < 0:
        loose = weighed and not product['is_catch_weight']
        _trim_reservations(batch.pk, changes['weight_kg'] if loose else changes['quantity'], loose)
    Product.objects.filter(pk=batch.product_id).update(  # updated_at: tills see stock change (G26 catalogue)
        stock_quantity=F('stock_quantity') + sign * quantity, updated_at=timezone.now())  # the app clock, like save()
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
        take = min(batch.free, remaining)
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
            held = batch.free  # reserved kg stays in the batch
            if index == len(batches) - 1 and held < remaining:
                move(batch, 0, 'inbound', counterpart=adjustment, weight_kg=remaining - held)
                held = remaining
            take = min(held, remaining)
            move(batch, 0, 'outbound', counterpart=destination, weight_kg=take)
            allocations.append((batch, 0, take))
            remaining -= take
        return allocations

    batches = list(_fefo_batches(product, locations, today))
    available = sum(batch.free for batch in batches)
    if available < quantity:
        raise LedgerError(f'Only {available} of {product} in date here.', available=available)
    allocations, pieces_left, kg_taken = [], quantity, Decimal('0')
    for batch in batches:
        if pieces_left <= 0:
            break
        pieces = min(batch.free, pieces_left)
        last_of_batch = pieces == batch.quantity  # the real count: never while pieces are reserved
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


def take_short(product, *, quantity, weight_kg, location, destination, reference, expiry):
    """
    Sell what the shop no longer had (an offline sale synced later, G28): book ``quantity`` units (pieces for
    catch-weight) and ``weight_kg`` (weighed products) in from ADJUSTMENT to the ``SHORT-<reference>`` batch at
    ``location`` (created empty if missing, expiring ``expiry``), then out to ``destination``. The batch ends where it
    started, so nothing goes below 0 and both movements balance. Inside ``transaction.atomic()``. Returns
    ``(batch, pieces, kg)`` like ``take_weighed``'s entries (``kg`` None for each products).
    """
    weighed = product.is_weighed
    batch, _ = StockBatch.objects.get_or_create(
        product=product, batch_number=f'SHORT-{reference}', location=location,
        defaults={'quantity': 0, 'weight_kg': Decimal('0.000'), 'expiration_date': expiry},
    )
    batch = StockBatch.objects.select_for_update(of=('self',)).select_related('location').get(pk=batch.pk)
    kg = weight_kg if weighed else None
    adjustment = virtual_location(product.company_id, Location.ADJUSTMENT)
    move(batch, quantity, 'inbound', counterpart=adjustment, weight_kg=kg)
    move(batch, quantity, 'outbound', counterpart=destination, weight_kg=kg)
    return batch, quantity, kg


def _kg(value):
    return value.quantize(KG, rounding=ROUND_HALF_UP)


def _fefo_batches(product, locations, today=None, by='quantity'):
    """
    The product's in-date batches with free stock (``by``: pieces, or ``weight_kg`` for loose products) at
    ``locations``, in FEFO order, locked (inside a transaction). Each has ``free``: what it holds minus what is
    reserved (G41).
    """
    today = today or timezone.localdate()
    batches = list(
        StockBatch.objects.select_for_update(of=('self',))  # lock the batches, not the joined locations
        .select_related('location')
        .filter(product=product, location__in=locations, expiration_date__gte=today, **{f'{by}__gt': 0})
        .order_by('expiration_date', 'receive_date', 'pk')
    )
    # Read after the locks are held (a new statement), so a reservation committed while we waited is seen.
    reserved = reserved_by_batch([batch.pk for batch in batches], loose=by == 'weight_kg')
    for batch in batches:
        batch.free = getattr(batch, by) - reserved.get(batch.pk, 0)
    return [batch for batch in batches if batch.free > 0]


def reserved_by_batch(batch_ids, *, loose):
    """``{batch id: reserved amount}`` (kg for loose products, else units) of the active reservations."""
    if not batch_ids:
        return {}
    field = 'weight_kg' if loose else 'quantity'
    return dict(
        StockReservation.objects.filter(batch_id__in=batch_ids, released_at__isnull=True)
        .values('batch_id').annotate(total=Sum(field)).values_list('batch_id', 'total')
    )


def _trim_reservations(batch_id, holding, loose):
    """After stock left a (locked) batch: cut its active reservations, newest first, to what it now holds."""
    field = 'weight_kg' if loose else 'quantity'
    rows = list(StockReservation.objects.filter(batch_id=batch_id, released_at__isnull=True)
                .order_by('-created_at', '-id'))
    excess = sum((getattr(row, field) or 0) for row in rows) - holding
    for row in rows:
        if excess <= 0:
            break
        cut = min(getattr(row, field) or 0, excess)
        setattr(row, field, getattr(row, field) - cut)
        if not getattr(row, field):
            row.released_at = timezone.now()
        row.save(update_fields=[field, 'released_at'])
        excess -= cut


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
    batches = _fefo_batches(product, [source], today)
    available = sum(batch.free for batch in batches)
    if available < quantity:
        raise LedgerError(f'Only {available} unit(s) of {product} available at {source.code}.', available=available)
    allocations, remaining = [], quantity
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.free, remaining)
        allocations.append((batch, transfer_batch(batch, take, to, transfer=transfer), take))
        remaining -= take
    return allocations
