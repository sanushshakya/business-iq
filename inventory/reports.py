# inventory/reports.py

"""
Yield and waste reports (G24), on UK dates.

- Yield: completed breakdowns recorded between two dates (a voided one nets to zero in the ledger), grouped by input
  product, supplier (of the input batch) or butcher (who recorded it). Each output's actual kg against the template's
  expected % of the input kg, using the template's current percentages.
- Waste: write-offs written off between two dates (pending and rejected ones moved nothing), per week from Monday,
  grouped by reason or product, with the known cost and how many had none.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from vat.services import UK

from .models import Breakdown, WriteOff, YieldTemplateOutput

UTC = dt_timezone.utc
KG, CENTS = Decimal('0.001'), Decimal('0.01')
ZERO_KG, ZERO = Decimal('0.000'), Decimal('0.00')
YIELD_GROUPS = ('product', 'supplier', 'butcher')
WASTE_GROUPS = ('reason', 'product')
NO_SUPPLIER, NO_BUTCHER = 'Supplier not recorded', 'Not recorded'


def uk_range(start, end):
    """The instants from UK midnight at the start of ``start`` up to (not including) UK midnight after ``end``."""
    return (datetime.combine(start, time.min, UK), datetime.combine(end + timedelta(days=1), time.min, UK))


def week_start(day):
    return day - timedelta(days=day.weekday())


def _share(kg, percent):
    return (kg * percent / 100).quantize(KG, rounding=ROUND_HALF_UP)


def _percent(part, whole):
    return (part * 100 / whole).quantize(CENTS, rounding=ROUND_HALF_UP) if whole else ZERO


def _yield_group(breakdown, group):
    if group == 'product':
        product = breakdown.input_batch.product
        return product.pk, product.name
    if group == 'supplier':
        supplier = breakdown.input_batch.supplier
        return (supplier.pk, supplier.name) if supplier else (None, NO_SUPPLIER)
    user = breakdown.created_by
    return (user.pk, user.name or f'User #{user.pk}') if user else (None, NO_BUTCHER)  # never the email


def _output_key(kind, product_id, name):
    return (kind, product_id) if kind == YieldTemplateOutput.PRODUCT else (kind, name)


def yield_report(company, start, end, group):
    since, until = uk_range(start, end)
    breakdowns = (
        Breakdown.objects.filter(company=company, status=Breakdown.COMPLETED, created_at__gte=since,
                                 created_at__lt=until)
        .select_related('input_batch__product', 'input_batch__supplier', 'created_by')
        .prefetch_related('lines', 'template__outputs__product')
        .order_by('pk')
    )
    groups = {}
    for breakdown in breakdowns:
        key, name = _yield_group(breakdown, group)
        row = groups.setdefault(key, {'key': key, 'name': name, 'breakdowns': 0, 'input_kg': ZERO_KG,
                                      'loss_kg': ZERO_KG, 'expected_loss_kg': ZERO_KG, 'input_cost': None, 'uncosted': 0,
                                      'outputs': {}})
        row['breakdowns'] += 1
        row['input_kg'] += breakdown.input_kg
        row['loss_kg'] += breakdown.loss_kg
        if breakdown.input_cost is None:
            row['uncosted'] += 1
        else:
            row['input_cost'] = (row['input_cost'] or ZERO) + breakdown.input_cost
        outputs = list(breakdown.template.outputs.all())
        row['expected_loss_kg'] += _share(breakdown.input_kg, breakdown.template.percent_totals(outputs)[2])
        for output in outputs:
            entry = _output(row, output.kind, output.product_id, output.label)
            entry['expected_kg'] += _share(breakdown.input_kg, output.expected_percent)
        for line in breakdown.lines.all():
            _output(row, line.kind, line.product_id, line.name)['actual_kg'] += line.weight_kg
    rows = sorted(groups.values(), key=lambda row: (row['key'] is None, row['name'].lower()))
    for row in rows:
        outputs = sorted(row['outputs'].values(),
                         key=lambda o: (o['kind'] != YieldTemplateOutput.PRODUCT, o['name'].lower()))
        for entry in outputs:
            entry['actual_percent'] = _percent(entry['actual_kg'], row['input_kg'])
            entry['expected_percent'] = _percent(entry['expected_kg'], row['input_kg'])
            entry['variance_kg'] = entry['actual_kg'] - entry['expected_kg']
        row['outputs'] = outputs
    return {'from': start, 'to': end, 'group': group, 'rows': rows}


def _output(row, kind, product_id, name):
    return row['outputs'].setdefault(_output_key(kind, product_id, name), {
        'kind': kind, 'product': product_id if kind == YieldTemplateOutput.PRODUCT else None, 'name': name,
        'actual_kg': ZERO_KG, 'expected_kg': ZERO_KG,
    })


def waste_report(company, start, end, group):
    since, until = uk_range(start, end)
    write_offs = (
        WriteOff.objects.filter(company=company, status=WriteOff.WRITTEN_OFF, written_off_at__gte=since,
                                written_off_at__lt=until)
        .select_related('batch__product')
        .order_by('written_off_at', 'pk')
    )
    reasons = dict(WriteOff.REASONS)
    weeks = defaultdict(dict)
    for write_off in write_offs:
        week = week_start(timezone.localtime(write_off.written_off_at, UK).date())
        if group == 'reason':
            key, name = write_off.reason, reasons[write_off.reason]
        else:
            key, name = write_off.batch.product_id, write_off.batch.product.name
        row = weeks[week].setdefault(key, {'key': key, 'name': name, 'write_offs': 0, 'quantity': 0,
                                           'weight_kg': ZERO_KG, 'cost': ZERO, 'uncosted': 0})
        row['write_offs'] += 1
        row['quantity'] += write_off.quantity
        row['weight_kg'] += write_off.weight_kg or ZERO_KG
        if write_off.cost is None:
            row['uncosted'] += 1
        else:
            row['cost'] += write_off.cost
    result = []
    for week in sorted(weeks):
        rows = sorted(weeks[week].values(), key=lambda row: (-row['cost'], row['name'].lower()))
        result.append({'week_start': week, 'rows': rows, 'total_cost': sum((r['cost'] for r in rows), ZERO)})
    return {'from': start, 'to': end, 'group': group, 'weeks': result,
            'total_cost': sum((w['total_cost'] for w in result), ZERO)}
