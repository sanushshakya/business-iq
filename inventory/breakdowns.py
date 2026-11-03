# inventory/breakdowns.py

"""
Butchery breakdowns (G21): an input batch at a butchery location cut into a yield template's products and waste.

``record_breakdown`` books it in one step through the ledger, so ``input_kg`` = product kg + waste kg + loss exactly:

- the input batch sends the product kg (and, for catch-weight, the pieces) out to its own location, where each
  product line gets a new batch (number = the breakdown's reference) holding its kg: a conversion in place, so stock
  at the location stays "in − out" per product;
- each waste line goes out of the input batch to WASTE, and any loss to ADJUSTMENT.

The input's landed cost (kg × the batch's ``unit_cost``, to the penny) is spread over the product lines by kg or by
value (kg × price per kg), per the template's ``cost_method``: each share half-up to the penny, and whatever rounding
leaves over (or takes too much) goes on the largest output, so the shares sum to the input cost exactly. Waste carries
none; each output batch gets its share ÷ kg as its ``unit_cost``.

``void_breakdown`` writes the opposite of every one of those movements, once the outputs still hold what they got.
Every movement carries ``breakdown``.
"""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from vat.services import uk_date

from .ledger import KG, move, virtual_location
from .models import Breakdown, BreakdownLine, Location, StockBatch, StockMovement, YieldTemplate, YieldTemplateOutput
from .models import weighed_product_problem
from .transfers import next_number

EXPECTED_LOSS_REASON = 'Expected loss (template)'
CENTS, COST_PER_KG = Decimal('0.01'), Decimal('0.0001')


class BreakdownError(Exception):
    """A breakdown that cannot be booked; ``detail`` maps fields to messages (a DRF-style 400 body)."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def record_breakdown(*, company, user, template, location, input_batch, lines, input_kg=None, input_pieces=None,
                     loss_reason='', client_ref=None):
    """
    Book a breakdown and return ``(breakdown, created)``. ``lines`` is ``[(template output, weight_kg[, pieces[,
    expiration_date]]), ...]``: outputs left out produced nothing. A loose input sends ``input_kg``; a catch-weight
    one sends ``input_pieces`` (their kg is the batch's average per piece). A repeated ``client_ref`` returns the
    first breakdown and moves nothing. Raises ``BreakdownError``.
    """
    if client_ref is not None:
        existing = Breakdown.objects.filter(company=company, client_ref=client_ref).first()
        if existing:
            return existing, False
    try:
        with transaction.atomic():
            return _record(company, user, template, location, input_batch, lines, input_kg, input_pieces,
                           loss_reason, client_ref)
    except IntegrityError:
        if client_ref is None:
            raise
        existing = Breakdown.objects.filter(company=company, client_ref=client_ref).first()
        if existing is None:
            raise
        return existing, False  # the same request booked meanwhile


def _record(company, user, template, location, input_batch, lines, input_kg, input_pieces, loss_reason, client_ref):
    today = uk_date()
    batch = StockBatch.objects.select_for_update(of=('self',)).select_related('location', 'product').get(
        pk=input_batch.pk)
    if client_ref is not None:  # the same request may have been booked while this one waited for the lock
        existing = Breakdown.objects.filter(company=company, client_ref=client_ref).first()
        if existing:
            return existing, False
    _check_source(company, template, location, batch, today)
    input_pieces, input_kg = _input(batch, input_kg, input_pieces)
    entries = _lines(template, lines, batch, today)

    out = sum((weight for _, weight, _, _ in entries), Decimal('0.000'))
    loss = input_kg - out
    if loss < 0:
        raise BreakdownError({'lines': [f'Outputs and waste weigh {-loss} kg more than the input; check the weights.']})
    expected = (input_kg * template.expected_loss_percent / 100).quantize(KG, rounding=ROUND_HALF_UP)
    loss_reason = (loss_reason or '').strip()
    if loss > expected and not loss_reason:
        raise BreakdownError({'loss_reason': [
            f'Loss is {loss} kg, more than the {expected} kg the template expects; say why (e.g. trimmed for quality).']})
    if loss and not loss_reason:
        loss_reason = EXPECTED_LOSS_REASON

    products = [entry for entry in entries if entry[0].kind == YieldTemplateOutput.PRODUCT]
    input_cost = None if batch.unit_cost is None else (input_kg * batch.unit_cost).quantize(CENTS, ROUND_HALF_UP)
    shares = _allocate(template, products, input_cost)
    breakdown = Breakdown.objects.create(
        company=company, number=next_number(Breakdown, company), template=template, location=location,
        input_batch=batch, input_pieces=input_pieces, input_kg=input_kg, loss_kg=loss, loss_reason=loss_reason,
        input_cost=input_cost, cost_method=template.cost_method if input_cost is not None else '',
        client_ref=client_ref, created_by=user,
    )
    clashes = [f'{output.product.name} already has a batch {breakdown.reference} at {location.code}; rename that batch '
               'first.' for output, *_ in products if StockBatch.objects.filter(
                   product=output.product, batch_number=breakdown.reference, location=location).exists()]
    if clashes:
        raise BreakdownError({'lines': clashes})
    move(batch, input_pieces, 'outbound', counterpart=location, breakdown=breakdown,
         weight_kg=sum((weight for _, weight, _, _ in products), Decimal('0.000')))
    waste = virtual_location(company, Location.WASTE)
    for output, weight, pieces, expiry in entries:
        cost = shares.get(output.pk)
        line = BreakdownLine(breakdown=breakdown, template_output=output, kind=output.kind, product=output.product,
                             name=output.label, weight_kg=weight, pieces=pieces, expiration_date=expiry,
                             allocated_cost=cost)
        if output.kind == YieldTemplateOutput.PRODUCT:
            line.output_batch = StockBatch.objects.create(
                product=output.product, batch_number=breakdown.reference, location=location, quantity=0,
                weight_kg=Decimal('0.000'), expiration_date=expiry,
                unit_cost=None if cost is None else (cost / weight).quantize(COST_PER_KG, ROUND_HALF_UP))
            move(line.output_batch, pieces, 'inbound', counterpart=location, weight_kg=weight, breakdown=breakdown)
        else:
            move(batch, 0, 'outbound', counterpart=waste, weight_kg=weight, breakdown=breakdown)
        line.save()
    if loss:
        move(batch, 0, 'outbound', counterpart=virtual_location(company, Location.ADJUSTMENT), weight_kg=loss,
             breakdown=breakdown)
    return breakdown, True


def _allocate(template, products, input_cost):
    """``{output id: share}`` of ``input_cost`` over the product lines (empty when there is no cost to spread)."""
    if input_cost is None:
        return {}
    if template.cost_method == YieldTemplate.BY_VALUE:
        missing = [output.product.name for output, *_ in products if not output.product.price_per_kg]
        if missing:
            raise BreakdownError({'template': [
                f'{name} has no price per kg, which spreading the cost by value needs. Set it, or spread this '
                'template by weight.' for name in missing]})
        basis = {output.pk: weight * output.product.price_per_kg for output, weight, *_ in products}
    else:
        basis = {output.pk: weight for output, weight, *_ in products}
    total = sum(basis.values())
    shares = {pk: (input_cost * part / total).quantize(CENTS, ROUND_HALF_UP) for pk, part in basis.items()}
    largest = max(basis, key=lambda pk: basis[pk])  # the first of equals: dicts keep the lines' order
    shares[largest] += input_cost - sum(shares.values())
    return shares


def _check_source(company, template, location, batch, today):
    # The API only offers the user's own rows; checked here too because superusers are not scoped.
    for field, row_company in (('template', template.company_id), ('location', location.company_id),
                               ('input_batch', batch.product.company_id)):
        if row_company != company.pk:
            raise BreakdownError({field: ['Invalid pk - object does not exist.']})
    if location.type != Location.BUTCHERY or not location.is_active:
        raise BreakdownError({'location': [f'{location.name} is not a butchery location.'
                                           if location.type != Location.BUTCHERY else f'{location.name} is closed.']})
    if not template.is_active:
        raise BreakdownError({'template': [f'{template.name} is not in use.']})
    # Checked again here: a product may have changed to "sold by each" since the template was saved.
    products = [template.input_product] + [o.product for o in template.outputs.all() if o.product_id]
    problems = [problem for problem in map(weighed_product_problem, products) if problem]
    if problems:
        raise BreakdownError({'template': problems})
    if batch.product_id != template.input_product_id:
        raise BreakdownError({'input_batch': [
            f'This template breaks down {template.input_product.name}, not {batch.product.name}.']})
    if batch.location_id != location.pk:
        raise BreakdownError({'input_batch': [
            f'Batch {batch.batch_number} is at {batch.location.code}, not {location.code}; transfer it first.']})
    if batch.expiration_date < today:
        raise BreakdownError({'input_batch': [
            f'Batch {batch.batch_number} expired on {batch.expiration_date:%d %b %Y}; it cannot be broken down.']})


def _input(batch, input_kg, input_pieces):
    """``(pieces, kg)`` taken from the (locked) input batch."""
    if batch.product.is_catch_weight:
        if input_kg is not None:
            raise BreakdownError({'input_kg': ['Send the pieces; their kg comes from the batch.']})
        if not input_pieces or input_pieces < 1:
            raise BreakdownError({'input_pieces': ['Say how many pieces go in.']})
        if input_pieces > batch.quantity:
            raise BreakdownError({'input_pieces': [f'Batch {batch.batch_number} holds {batch.quantity} piece(s).']})
        if input_pieces == batch.quantity:  # the last pieces take all the kg left
            return input_pieces, batch.weight_kg
        return input_pieces, (batch.weight_kg * input_pieces / batch.quantity).quantize(KG, rounding=ROUND_HALF_UP)
    if input_pieces:
        raise BreakdownError({'input_pieces': [f'{batch.product.name} is sold loose; send the kg.']})
    if input_kg is None or input_kg <= 0 or input_kg != input_kg.quantize(KG):
        raise BreakdownError({'input_kg': ['Say how many kg go in (up to 3 decimals).']})
    if input_kg > batch.weight_kg:
        raise BreakdownError({'input_kg': [f'Batch {batch.batch_number} holds {batch.weight_kg} kg.']})
    return 0, input_kg


def _lines(template, lines, batch, today):
    """``[(output, kg, pieces, expiry)]`` checked against the template; raises with every problem found."""
    problems, entries, seen = [], [], set()
    template_outputs = {o.pk: o for o in template.outputs.select_related('product')}
    for output, weight, *rest in lines:
        pieces = (rest[0] if rest else 0) or 0
        expiry = rest[1] if len(rest) > 1 else None
        if output.pk not in template_outputs:
            problems.append(f'{output.label} is not an output of {template.name}.')
            continue
        output = template_outputs[output.pk]
        if output.pk in seen:
            problems.append(f'{output.label} is listed twice.')
            continue
        seen.add(output.pk)
        if weight is None or weight <= 0 or weight != weight.quantize(KG):
            problems.append(f'{output.label}: weigh it (more than 0 kg, up to 3 decimals).')
            continue
        if output.kind == YieldTemplateOutput.WASTE:
            if pieces or expiry:
                problems.append(f'{output.label} is waste; send only its kg.')
            entries.append((output, weight, 0, None))
            continue
        product = output.product
        if product.is_catch_weight and pieces < 1:
            problems.append(f'{product.name}: say how many pieces.')
        elif not product.is_catch_weight and pieces:
            problems.append(f'{product.name} is sold loose; leave the pieces out.')
        latest = batch.expiration_date
        if output.keeps_days is not None:
            latest = min(latest, today + timedelta(days=output.keeps_days))
        if expiry is None:
            expiry = latest
        elif expiry > latest:
            problems.append(f'{product.name} can expire on {latest:%d %b %Y} at the latest.')
        elif expiry < today:
            problems.append(f"{product.name}'s expiry cannot be in the past.")
        entries.append((output, weight, pieces, expiry))
    if not problems and not any(output.kind == YieldTemplateOutput.PRODUCT for output, *_ in entries):
        problems.append('Record what came out: at least one product with its weight.')
    if problems:
        raise BreakdownError({'lines': problems})
    return entries


def void_breakdown(breakdown, *, user, reason):
    """Reverse every movement of a completed breakdown (the outputs must still hold what they got)."""
    reason = (reason or '').strip()
    if not reason:
        raise BreakdownError({'reason': ['Say why the breakdown is being voided.']})
    with transaction.atomic():
        breakdown = Breakdown.objects.select_for_update().get(pk=breakdown.pk)
        if breakdown.status != Breakdown.COMPLETED:
            raise BreakdownError({'detail': [f'{breakdown.reference} is already voided.']})
        movements = list(StockMovement.objects.filter(breakdown=breakdown).select_related(
            'source_location', 'destination_location').order_by('pk'))
        batches = {b.pk: b for b in StockBatch.objects.select_for_update(of=('self',)).select_related(
            'location', 'product').filter(pk__in={m.batch_id for m in movements}).order_by('pk')}  # fixed lock order
        for line in breakdown.lines.filter(output_batch__isnull=False):
            held = batches[line.output_batch_id]
            gone_kg, gone_pieces = line.weight_kg - held.weight_kg, line.pieces - held.quantity
            if gone_kg > 0 or gone_pieces > 0:
                gone = f'{gone_kg} kg' if gone_kg > 0 else f'{gone_pieces} piece(s)'
                raise BreakdownError({'detail': [
                    f'{gone} of {line.name} from {breakdown.reference} has gone (sold or moved); it cannot be '
                    'voided.']})
        changed = [b.product.name for b in batches.values() if not b.product.is_weighed]
        if changed:  # possible once a batch is empty: the ledger would refuse kg for it
            raise BreakdownError({'detail': [f'{", ".join(sorted(set(changed)))} is no longer sold by weight; '
                                             'it cannot be voided.']})
        for movement in movements:
            batch = batches[movement.batch_id]
            if movement.movement_type == 'outbound':
                move(batch, movement.quantity, 'inbound', counterpart=movement.destination_location,
                     weight_kg=movement.weight_kg, breakdown=breakdown)
            else:
                move(batch, movement.quantity, 'outbound', counterpart=movement.source_location,
                     weight_kg=movement.weight_kg, breakdown=breakdown)
        breakdown.status, breakdown.voided_by, breakdown.voided_at = Breakdown.VOIDED, user, timezone.now()
        breakdown.void_reason = reason
        breakdown.save(update_fields=['status', 'voided_by', 'voided_at', 'void_reason'])
    return breakdown
