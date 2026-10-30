# inventory/requisitions.py

"""
Store requisitions: a store location asks for stock, and the warehouse fulfils it with draft transfers (in full or in
parts). A line's fulfilled amount is what sits on its transfers, except cancelled ones; the rest stays open.
Requisitions never move stock themselves: their transfers do (``inventory.transfers``).
"""

from django.db import transaction
from django.utils import timezone

from . import transfers
from .models import Requisition, RequisitionLine


class RequisitionError(Exception):
    """A request the requisition rules refuse; ``detail`` is DRF-style (field → messages)."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def _validate_location(company, location):
    if location.company_id != company.pk:
        raise RequisitionError({'location': ['This location belongs to another company.']})
    if location.is_virtual:
        raise RequisitionError({'location': ['A requisition is raised by a real location, not a virtual one.']})
    if not location.is_active:
        raise RequisitionError({'location': [f'{location.code} is no longer in use.']})


@transaction.atomic
def create_requisition(*, company, user, location, lines, note=''):
    """A new open requisition. ``lines`` is ``[(product, quantity), ...]``, one per product."""
    _validate_location(company, location)
    try:
        transfers._validate_lines(company, lines)  # same line rules as a transfer
    except transfers.TransferError as error:
        raise RequisitionError(error.detail) from None
    requisition = Requisition.objects.create(
        company=company, number=transfers.next_number(Requisition, company), location=location, note=note,
        created_by=user,
    )
    RequisitionLine.objects.bulk_create(
        RequisitionLine(requisition=requisition, product=product, quantity=quantity) for product, quantity in lines
    )
    return requisition


def _status_for(requisition):
    lines = list(requisition.lines.all())
    fulfilled = [line.fulfilled_quantity for line in lines]
    if all(done >= line.quantity for done, line in zip(fulfilled, lines)):
        return Requisition.FULFILLED
    return Requisition.PARTLY_FULFILLED if any(fulfilled) else Requisition.OPEN


def refresh_status(requisition_id):
    """Recompute an open requisition's status from its lines (after a fulfil or a cancelled transfer)."""
    requisition = Requisition.objects.select_for_update().get(pk=requisition_id)
    if requisition.status == Requisition.CANCELLED:
        return requisition
    status = _status_for(requisition)
    if status != requisition.status:
        requisition.status = status
        requisition.save(update_fields=['status'])
    return requisition


def _locked(requisition):
    locked = Requisition.objects.select_for_update(of=('self',)).select_related('location').get(pk=requisition.pk)
    if locked.status in (Requisition.FULFILLED, Requisition.CANCELLED):
        raise RequisitionError({'detail': [f'{locked.reference} is {locked.get_status_display().lower()}.']})
    return locked


@transaction.atomic
def fulfil(requisition, user, *, source, quantities):
    """
    Make a draft transfer from ``source`` to the requisition's location for ``quantities`` (``{line id: units}``;
    lines left out or 0 are not sent now). Each amount is at most what is still open on its line. Returns the transfer.
    """
    requisition = _locked(requisition)
    lines = {line.pk: line for line in requisition.lines.select_related('product')}
    picked = []
    for line_id, quantity in quantities.items():
        line = lines.get(line_id)
        if line is None:
            raise RequisitionError({'lines': ['Use lines of this requisition.']})
        if quantity < 0 or quantity > line.open_quantity:
            raise RequisitionError({'lines': [f'{line.product}: send between 0 and {line.open_quantity}.']})
        if quantity:
            picked.append((line.product, quantity, line))
    if not picked:
        raise RequisitionError({'lines': ['Send at least one unit.']})
    try:
        transfer = transfers.create_transfer(
            company=requisition.company, user=user, source=source, destination=requisition.location,
            lines=sorted(picked, key=lambda item: item[0].pk), note=f'For {requisition.reference}',
        )
    except transfers.TransferError as error:
        raise RequisitionError(error.detail) from None
    refresh_status(requisition.pk)
    return transfer


@transaction.atomic
def cancel(requisition, user):
    """Close what is still open. Transfers already made stay (cancel them on their own)."""
    requisition = _locked(requisition)
    requisition.status, requisition.cancelled_by, requisition.cancelled_at = (
        Requisition.CANCELLED, user, timezone.now())
    requisition.save(update_fields=['status', 'cancelled_by', 'cancelled_at'])
    return requisition
