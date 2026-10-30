# inventory/transfers.py

"""
Stock transfers between a company's own locations: draft → dispatched → received, or draft → cancelled.

Dispatch takes each line's units from in-date batches at the source, earliest expiry first, into the same lots at
the company's IN-TRANSIT location (``TransferPick`` remembers which). Receive books what arrived into the same lots
at the destination and sends the rest of each pick to ADJUSTMENT; any difference needs a reason. Stock only moves
through ``inventory.ledger``. Each step locks the transfer row and checks its status, so a repeat is refused.
"""

from django.db import transaction
from django.utils import timezone

from tenants.models import Company

from .ledger import LedgerError, move, transfer_batch, transfer_fefo, virtual_location
from .models import Location, StockBatch, Transfer, TransferLine, TransferPick


class TransferError(Exception):
    """A request the transfer rules refuse; ``detail`` is DRF-style (field → messages)."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def validate_route(company, source, destination):
    """Both ends are the company's own, real, active locations, and not the same one."""
    for field, location in (('source_location', source), ('destination_location', destination)):
        if location.company_id != company.pk:
            raise TransferError({field: ['This location belongs to another company.']})
        if location.is_virtual:
            raise TransferError({field: ['Transfers are between real locations, not virtual ones.']})
        if not location.is_active:
            raise TransferError({field: [f'{location.code} is no longer in use.']})
    if source.pk == destination.pk:
        raise TransferError({'destination_location': ['Choose a different location to send the stock to.']})


def _validate_lines(company, lines):
    if not lines:
        raise TransferError({'lines': ['Add at least one product.']})
    seen = set()
    for product, quantity in lines:
        if product.company_id != company.pk:
            raise TransferError({'lines': ['This product belongs to another company.']})
        if product.pk in seen:
            raise TransferError({'lines': [f'{product} is listed twice.']})
        if quantity < 1:
            raise TransferError({'lines': [f'{product}: send at least 1 unit.']})
        seen.add(product.pk)


def _write_lines(transfer, lines):
    TransferLine.objects.bulk_create(
        TransferLine(transfer=transfer, product=product, quantity=quantity) for product, quantity in lines
    )


@transaction.atomic
def create_transfer(*, company, user, source, destination, lines, note=''):
    """A new draft. ``lines`` is ``[(product, quantity), ...]``; the number is the company's next one."""
    validate_route(company, source, destination)
    _validate_lines(company, lines)
    Company.objects.select_for_update().get(pk=company.pk)  # one number at a time per company
    last = Transfer.objects.filter(company=company).order_by('-number').values_list('number', flat=True).first()
    transfer = Transfer.objects.create(
        company=company, number=(last or 0) + 1, source_location=source, destination_location=destination,
        note=note, created_by=user,
    )
    _write_lines(transfer, lines)
    return transfer


def _locked(transfer, status):
    """The transfer row, locked, if it is still ``status``."""
    locked = Transfer.objects.select_for_update().select_related('source_location', 'destination_location').get(
        pk=transfer.pk
    )
    if locked.status != status:
        raise TransferError({'detail': [f'{locked.reference} is {locked.get_status_display().lower()}.']})
    return locked


@transaction.atomic
def replace_draft(transfer, *, source, destination, lines, note=''):
    """Change a draft's route, note and lines (the lines are replaced)."""
    transfer = _locked(transfer, Transfer.DRAFT)
    validate_route(transfer.company, source, destination)
    _validate_lines(transfer.company, lines)
    transfer.source_location, transfer.destination_location, transfer.note = source, destination, note
    transfer.save(update_fields=['source_location', 'destination_location', 'note'])
    transfer.lines.all().delete()
    _write_lines(transfer, lines)
    return transfer


@transaction.atomic
def dispatch(transfer, user):
    """Send every line from the source to IN-TRANSIT; if any line is short, nothing moves."""
    transfer = _locked(transfer, Transfer.DRAFT)
    source = Location.objects.get(pk=transfer.source_location_id)  # as it is now (it may have been retired)
    destination = Location.objects.get(pk=transfer.destination_location_id)
    validate_route(transfer.company, source, destination)
    transit = virtual_location(transfer.company_id, Location.IN_TRANSIT)
    for line in transfer.lines.select_related('product'):
        try:
            allocations = transfer_fefo(line.product, line.quantity, source=source, to=transit, transfer=transfer)
        except LedgerError as error:
            raise TransferError({'lines': [
                f'{line.product}: only {error.available} available at {source.code}.'
            ]}) from None
        picked = {}
        for _, held, taken in allocations:
            picked[held.pk] = picked.get(held.pk, 0) + taken
        TransferPick.objects.bulk_create(
            TransferPick(line=line, transit_batch_id=batch_id, quantity=quantity) for batch_id, quantity in picked.items()
        )
    transfer.status, transfer.dispatched_by, transfer.dispatched_at = Transfer.DISPATCHED, user, timezone.now()
    transfer.save(update_fields=['status', 'dispatched_by', 'dispatched_at'])
    return transfer


def _received(transfer, entries):
    """``{line: (received, reason)}`` for every line exactly once, or ``TransferError``."""
    lines = {line.pk: line for line in transfer.lines.select_related('product')}
    result = {}
    for entry in entries:
        line = lines.get(entry['line'])
        if line is None or line in result:
            raise TransferError({'lines': ['Give each line of this transfer once.']})
        received, reason = entry['received_quantity'], (entry.get('reason') or '').strip()
        if not 0 <= received <= line.quantity:
            raise TransferError({'lines': [f'{line.product}: received must be between 0 and {line.quantity}.']})
        if received != line.quantity and not reason:
            raise TransferError({'lines': [f'{line.product}: say why {received} of {line.quantity} arrived.']})
        result[line] = (received, reason)
    if len(result) != len(lines):
        raise TransferError({'lines': ['Give each line of this transfer once.']})
    return result


@transaction.atomic
def receive(transfer, user, entries):
    """
    Book what arrived. ``entries`` is ``[{'line': <line id>, 'received_quantity': n, 'reason': '...'}, ...]`` with
    every line once; received is 0..dispatched and needs a reason when different. The rest goes to ADJUSTMENT.
    """
    transfer = _locked(transfer, Transfer.DISPATCHED)
    received = _received(transfer, entries)
    destination = transfer.destination_location
    adjustment = virtual_location(transfer.company_id, Location.ADJUSTMENT)
    for line, (quantity, reason) in received.items():
        remaining = quantity
        for pick in line.picks.select_related('transit_batch').order_by('transit_batch__expiration_date', 'pk'):
            held = StockBatch.objects.select_for_update(of=('self',)).select_related('location').get(
                pk=pick.transit_batch_id
            )
            arrived = min(pick.quantity, remaining)
            if arrived:
                transfer_batch(held, arrived, destination, transfer=transfer)
            if pick.quantity > arrived:
                move(held, pick.quantity - arrived, 'outbound', counterpart=adjustment, transfer=transfer)
            remaining -= arrived
        line.received_quantity, line.difference_reason = quantity, reason
        line.save(update_fields=['received_quantity', 'difference_reason'])
    transfer.status, transfer.received_by, transfer.received_at = Transfer.RECEIVED, user, timezone.now()
    transfer.save(update_fields=['status', 'received_by', 'received_at'])
    return transfer


@transaction.atomic
def cancel(transfer, user):
    """Cancel a draft (nothing has moved). A dispatched transfer is closed by receiving it."""
    transfer = _locked(transfer, Transfer.DRAFT)
    transfer.status, transfer.cancelled_by, transfer.cancelled_at = Transfer.CANCELLED, user, timezone.now()
    transfer.save(update_fields=['status', 'cancelled_by', 'cancelled_at'])
    return transfer
