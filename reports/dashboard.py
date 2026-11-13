# reports/dashboard.py

"""
The consolidated dashboard (G59): sales, margin, waste and stock value for one week against the week before, read
from what the system already records (workspace spec ``docs/superpowers/specs/2026-11-13-g59-consolidated-dashboard-
design.md``). Read only; money is ex VAT.

- A week runs Monday–Sunday on UK dates; a week still running is compared with the same days of the week before.
- Stock value at an instant is rebuilt from the ledger: what each batch holds now, minus the movements after the
  instant (0 for a lot received after it), × the batch's landed ``unit_cost`` (per kg for weighed products).
"""

from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

from inventory.models import Location, StockBatch, StockMovement
from inventory.reports import week_start

CENTS = Decimal('0.01')
ZERO, ZERO_KG = Decimal('0.00'), Decimal('0.000')
# Virtual locations hold nothing of ours, except stock on the road between two of our places.
NOT_STOCK = tuple(t for t in Location.VIRTUAL_TYPES if t != Location.IN_TRANSIT)
NO_BRANCH = 'No branch'


def money(amount):
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def week_bounds(day, today):
    """``(start, end_day, previous_start, previous_end_day)`` for the week holding ``day``: up to ``today`` while it
    runs, and the same days of the week before."""
    start = week_start(day)
    end_day = min(start + timedelta(days=6), today)
    if end_day < start:  # a week in the future: nothing has happened yet
        end_day = start
    days = (end_day - start).days
    return start, end_day, start - timedelta(days=7), start - timedelta(days=7) + timedelta(days=days)


def _place(location):
    branch = location.branch
    return {'location': location.pk, 'location_code': location.code, 'location_name': location.name,
            'branch': branch.pk if branch else None, 'branch_name': branch.name if branch else NO_BRANCH}


def stock_value(company, at):
    """Per location holding stock at the instant ``at``: ``value`` (costed stock) and what had no cost
    (``uncosted_units`` / ``uncosted_kg``), by location code."""
    batches = (StockBatch.objects.filter(product__company=company, receive_date__lt=at)
               .exclude(location__type__in=NOT_STOCK)
               .select_related('product', 'location__branch'))
    later = defaultdict(lambda: [0, ZERO_KG])  # what moved in minus out after ``at``, per batch
    rows = (StockMovement.objects.filter(batch__product__company=company, date_time__gte=at)
            .values('batch_id', 'movement_type')
            .annotate(units=Sum('quantity'), kg=Sum('weight_kg')))
    for row in rows:
        sign = 1 if row['movement_type'] == 'inbound' else -1
        later[row['batch_id']][0] += sign * (row['units'] or 0)
        later[row['batch_id']][1] += sign * (row['kg'] or ZERO_KG)
    places = {}
    for batch in batches:
        units, kg = later.get(batch.pk, (0, ZERO_KG))
        units, kg = batch.quantity - units, batch.weight_kg - kg
        if not units and not kg:
            continue
        place = places.setdefault(batch.location_id, {**_place(batch.location), 'value': ZERO, 'uncosted_units': 0,
                                                      'uncosted_kg': ZERO_KG})
        weighed = batch.product.is_weighed
        if batch.unit_cost is None:
            if weighed:
                place['uncosted_kg'] += kg
            else:
                place['uncosted_units'] += units
            continue
        place['value'] += money((kg if weighed else units) * batch.unit_cost)
    return sorted(places.values(), key=lambda row: (row['branch_name'], row['location_code']))
