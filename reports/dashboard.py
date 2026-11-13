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

from inventory.models import Location, Product, StockBatch, StockMovement, WriteOff
from inventory.reports import uk_range, week_start
from sales.models import Sale, SaleAllocation, SaleItem
from sync.models import InboundEvent
from vat.services import UK, NoVatRate, rate_for, split_inclusive
from wholesale.models import CreditNote, CreditNoteLine, Invoice, InvoiceLine, SalesOrderPick

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


TILL, TRADE, ONLINE = 'till', 'trade', 'online'
CHANNEL_ORDER = {TILL: 0, TRADE: 1, ONLINE: 2}
ONLINE_NAME = 'Online shop'


def _cost(amount, unit_cost):
    return None if unit_cost is None else money(amount * unit_cost)


class _Rows:
    """Sales rows keyed by (channel, branch, location)."""

    def __init__(self):
        self.rows = {}

    def add(self, key, place, *, net, cost, units=0, kg=ZERO_KG):
        row = self.rows.setdefault(key, {
            'channel': key[0], **place, 'net': ZERO, 'costed_net': ZERO, 'cost': ZERO, 'uncosted_net': ZERO,
            'units': 0, 'kg': ZERO_KG})
        row['net'] += net
        row['units'] += units
        row['kg'] += kg
        if cost is None:
            row['uncosted_net'] += net
        else:
            row['costed_net'] += net
            row['cost'] += cost

    def result(self):
        rows = []
        for row in self.rows.values():
            row['margin'] = row['costed_net'] - row['cost']
            row['margin_percent'] = (money(row['margin'] * 100 / row['costed_net']) if row['costed_net'] else None)
            rows.append(row)
        return sorted(rows, key=lambda r: (CHANNEL_ORDER[r['channel']], r['branch_name'], r['location_code'] or ''))


def _named_place(name, branch=None):
    return {'branch': branch, 'branch_name': name, 'location': None, 'location_code': None, 'location_name': None}


def _till(company, start, end_day, rows):
    since, until = uk_range(start, end_day)
    items = (SaleItem.objects.filter(sale__session__till__branch__company=company, sale__status=Sale.COMPLETED,
                                     sale__created_at__gte=since, sale__created_at__lt=until)
             .select_related('product', 'sale__session__till__branch'))
    costs = defaultdict(lambda: ZERO)
    uncosted = set()
    allocations = (SaleAllocation.objects.filter(item__in=items.values('pk'))
                   .values('item_id', 'quantity', 'weight_kg', 'batch__unit_cost', 'batch__product__sold_by'))
    for allocation in allocations:
        weighed = allocation['batch__product__sold_by'] == 'weight'
        cost = _cost(allocation['weight_kg'] if weighed else allocation['quantity'], allocation['batch__unit_cost'])
        if cost is None:
            uncosted.add(allocation['item_id'])
        else:
            costs[allocation['item_id']] += cost
    for item in items:
        branch = item.sale.session.till.branch
        weighed = item.weight_kg is not None
        net = item.net_amount if item.net_amount is not None else item.line_total  # before VAT was recorded (G07)
        known = item.pk in costs and item.pk not in uncosted
        rows.add((TILL, branch.pk, None), _named_place(branch.name, branch.pk), net=net, cost=costs[item.pk] if known else None,
                 units=0 if weighed else item.quantity, kg=item.weight_kg if weighed else ZERO_KG)


def _picked_cost(company, order_line_ids):
    """Average landed cost per unit (per kg for weighed) of what was picked for each order line; None when any pick
    came from a lot with no cost."""
    totals = defaultdict(lambda: [ZERO, ZERO_KG, False])  # cost, amount, uncosted
    picks = (SalesOrderPick.objects.filter(line_id__in=order_line_ids, line__order__company=company)
             .values('line_id', 'quantity', 'weight_kg', 'batch__unit_cost', 'batch__product__sold_by'))
    for pick in picks:
        weighed = pick['batch__product__sold_by'] == 'weight'
        amount = Decimal(pick['weight_kg'] or 0) if weighed else Decimal(pick['quantity'])
        total = totals[pick['line_id']]
        if pick['batch__unit_cost'] is None:
            total[2] = True
        else:
            total[0] += amount * pick['batch__unit_cost']
            total[1] += amount
    return {line: (None if uncosted or not amount else cost / amount) for line, (cost, amount, uncosted)
            in totals.items()}


def _trade(company, start, end_day, rows):
    invoice_lines = list(InvoiceLine.objects.filter(
        invoice__company=company, invoice__status=Invoice.ISSUED, invoice__invoice_date__gte=start,
        invoice__invoice_date__lte=end_day).select_related('product', 'invoice__order__source_location__branch'))
    credit_lines = list(CreditNoteLine.objects.filter(
        credit_note__company=company, credit_note__status=CreditNote.ISSUED, credit_note__credit_date__gte=start,
        credit_note__credit_date__lte=end_day).select_related(
        'invoice_line__product', 'credit_note__invoice__order__source_location__branch'))
    averages = _picked_cost(company, {line.order_line_id for line in invoice_lines}
                            | {line.invoice_line.order_line_id for line in credit_lines})
    for line, invoice_line, sign in ([(line, line, 1) for line in invoice_lines]
                                     + [(line, line.invoice_line, -1) for line in credit_lines]):
        weighed = invoice_line.product.is_weighed
        amount = Decimal(line.weight_kg or 0) if weighed else Decimal(line.quantity or 0)
        average = averages.get(invoice_line.order_line_id)
        location = (line.invoice if sign > 0 else line.credit_note.invoice).order.source_location
        place = _place(location) if location else _named_place(NO_BRANCH)
        cost = None if average is None else sign * money(amount * average)
        rows.add((TRADE, place['branch'], place['location']), place, net=sign * line.net, cost=cost,
                 units=0 if weighed else sign * (line.quantity or 0),
                 kg=sign * Decimal(line.weight_kg or 0) if weighed else ZERO_KG)


def _online(company, start, end_day, rows):
    since, until = uk_range(start, end_day)
    events = InboundEvent.objects.filter(connection__company=company, event_type='order.paid',
                                         status__in=('applied', 'partial'), received_at__gte=since,
                                         received_at__lt=until)
    lines = []
    for event in events:
        data = event.payload.get('data') if isinstance(event.payload, dict) else None
        day = event.received_at.astimezone(UK).date()
        for item in (data or {}).get('items') or []:
            if isinstance(item, dict) and isinstance(item.get('product_id'), str):
                lines.append((item, day))
    products = {p.external_id: p for p in Product.objects.filter(
        company=company, external_id__in={item['product_id'] for item, _ in lines})}
    place = _named_place(ONLINE_NAME)
    for item, day in lines:
        product = products.get(item['product_id'])
        quantity = item.get('quantity')
        try:
            gross = money(Decimal(str(item.get('unit_price'))) * quantity)
        except Exception:  # noqa: BLE001 - a malformed line from the shop is left out, never a 500
            continue
        if product is None or type(quantity) is not int or quantity < 1:
            continue
        try:
            net = gross - split_inclusive(gross, rate_for(product.vat_code, day))[1]
        except NoVatRate:
            net = gross
        # The shop's orders are taken FEFO without a link to the event: no batch, so no cost (yet).
        rows.add((ONLINE, None, None), place, net=net, cost=None, units=quantity)


def sales(company, start, end_day):
    """Sales rows for the UK days ``start``–``end_day``: till by branch, trade by warehouse, online."""
    rows = _Rows()
    _till(company, start, end_day, rows)
    _trade(company, start, end_day, rows)
    _online(company, start, end_day, rows)
    return rows.result()


def waste(company, start, end_day):
    """Write-offs written off in the UK days ``start``–``end_day``, by location."""
    since, until = uk_range(start, end_day)
    places = {}
    for write_off in (WriteOff.objects.filter(company=company, status=WriteOff.WRITTEN_OFF,
                                              written_off_at__gte=since, written_off_at__lt=until)
                      .select_related('location__branch', 'batch__product')):
        place = places.setdefault(write_off.location_id, {**_place(write_off.location), 'count': 0, 'units': 0,
                                                          'kg': ZERO_KG, 'cost': ZERO, 'uncosted': 0})
        place['count'] += 1
        place['units'] += write_off.quantity
        place['kg'] += write_off.weight_kg or ZERO_KG
        if write_off.cost is None:
            place['uncosted'] += 1
        else:
            place['cost'] += write_off.cost
    return sorted(places.values(), key=lambda row: (row['branch_name'], row['location_code']))


def _totals(rows, fields):
    return {field: sum((row[field] for row in rows), ZERO) for field in fields}


def _period(company, start, end_day):
    _since, until = uk_range(start, end_day)
    sold, wasted, stock = sales(company, start, end_day), waste(company, start, end_day), stock_value(company, until)
    totals = _totals(sold, ('net', 'costed_net', 'cost', 'uncosted_net'))
    totals['margin'] = totals['costed_net'] - totals['cost']
    totals['margin_percent'] = money(totals['margin'] * 100 / totals['costed_net']) if totals['costed_net'] else None
    totals['waste_cost'] = _totals(wasted, ('cost',))['cost']
    totals['stock_value'] = _totals(stock, ('value',))['value']
    return {'start': start, 'end': end_day, 'sales': sold, 'waste': wasted, 'stock': stock, 'totals': totals}


def dashboard(company, day, today):
    """The week holding ``day`` (to ``today`` while it runs) and the same days of the week before."""
    start, end_day, previous_start, previous_end = week_bounds(day, today)
    return {'week_start': start, 'end': end_day, 'previous_start': previous_start, 'previous_end': previous_end,
            'days': (end_day - start).days + 1, 'current': _period(company, start, end_day),
            'previous': _period(company, previous_start, previous_end)}
