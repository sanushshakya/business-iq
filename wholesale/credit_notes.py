"""
Credit notes (G45): a return credited against the order's issued invoice.

Each return line credits its invoice line at the invoice's unit price and VAT rate: units for each products, kg for
weighed ones; net and VAT rounded half up to the penny. An invoice line is never credited beyond what it billed — in
units or kg, and in money: the credit that brings a line's units/kg to what was invoiced takes exactly what is left
of its net and VAT, so a full return credits the invoice exactly. The cap is checked when the draft is made and again
when it is issued (under the invoice's lock, against issued credit notes). Issuing numbers it gap-free per company
(``CN-000001``) and freezes it; the PDF is emailed after commit, like invoices.
"""

import logging
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from tenants.models import Company
from vat.services import HUNDRED, PENNY, uk_date

from .invoices import recipient
from .models import CreditNote, CreditNoteLine, CustomerReturn, Invoice, InvoiceLine

logger = logging.getLogger(__name__)


class CreditNoteError(ValueError):
    """The credit note can't be made or issued; the message is for the API."""


def _round(value):
    return value.quantize(PENNY, rounding=ROUND_HALF_UP)


def _credited(invoice_line, *, issued_only, exclude=None):
    rows = CreditNoteLine.objects.filter(invoice_line=invoice_line)
    if issued_only:
        rows = rows.filter(credit_note__status=CreditNote.ISSUED)
    if exclude is not None:
        rows = rows.exclude(credit_note=exclude)
    totals = rows.aggregate(units=Sum('quantity'), kg=Sum('weight_kg'), net=Sum('net'), vat=Sum('vat'))
    return (totals['units'] or 0, totals['kg'] or Decimal('0'), totals['net'] or Decimal('0'),
            totals['vat'] or Decimal('0'))


def _credit_line(invoice_line, units, kg, *, already):
    """The credit for ``units``/``kg`` of ``invoice_line``, after ``already`` (units, kg, net, vat) credited."""
    done_units, done_kg, done_net, done_vat = already
    weighed = invoice_line.weight_kg is not None
    if weighed:
        if done_kg + kg > invoice_line.weight_kg or (invoice_line.quantity and done_units + units > invoice_line.quantity):
            raise CreditNoteError(f'{invoice_line.description}: more than the {invoice_line.weight_kg} kg invoiced.')
        amount, last = kg, done_kg + kg == invoice_line.weight_kg
    else:
        if done_units + units > invoice_line.quantity:
            raise CreditNoteError(f'{invoice_line.description}: more than the {invoice_line.quantity} invoiced.')
        amount, last = Decimal(units), done_units + units == invoice_line.quantity
    if last:  # the rest of the line, to the penny
        net, vat = invoice_line.net - done_net, invoice_line.vat - done_vat
    else:
        net = min(_round(amount * invoice_line.unit_price), invoice_line.net - done_net)
        vat = min(_round(net * invoice_line.vat_rate / HUNDRED), invoice_line.vat - done_vat)
    return CreditNoteLine(
        invoice_line=invoice_line, description=invoice_line.description,
        quantity=units if invoice_line.quantity is not None else None, weight_kg=kg if weighed else None,
        unit_price=invoice_line.unit_price, vat_code=invoice_line.vat_code, vat_rate=invoice_line.vat_rate,
        net=net, vat=vat, gross=net + vat,
    )


def _wanted(customer_return):
    """``{order line id: (units, kg)}`` given back on the return."""
    wanted = defaultdict(lambda: [0, Decimal('0')])
    for row in customer_return.lines.all():
        wanted[row.order_line_id][0] += row.quantity or 0
        wanted[row.order_line_id][1] += row.weight_kg or Decimal('0')
    return wanted


def _lines_for(credit_note_or_none, customer_return, invoice, *, issued_only):
    rows = []
    for order_line_id, (units, kg) in sorted(_wanted(customer_return).items()):
        invoice_line = InvoiceLine.objects.filter(invoice=invoice, order_line_id=order_line_id).first()
        if invoice_line is None:
            raise CreditNoteError('A returned line was not on the invoice, so there is nothing to credit for it.')
        already = _credited(invoice_line, issued_only=issued_only, exclude=credit_note_or_none)
        rows.append(_credit_line(invoice_line, units, kg, already=already))
    return rows


def _totals(credit_note, rows):
    by_rate = defaultdict(lambda: [Decimal('0'), Decimal('0')])
    for row in rows:
        by_rate[row.vat_rate][0] += row.net
        by_rate[row.vat_rate][1] += row.vat
    credit_note.net_total = sum((row.net for row in rows), Decimal('0'))
    credit_note.vat_total = sum((row.vat for row in rows), Decimal('0'))
    credit_note.gross_total = credit_note.net_total + credit_note.vat_total
    credit_note.vat_summary = [{'rate': str(rate), 'net': str(net), 'vat': str(vat)}
                               for rate, (net, vat) in sorted(by_rate.items())]


@transaction.atomic
def create_credit_note(customer_return, user):
    customer_return = CustomerReturn.objects.select_for_update().get(pk=customer_return.pk)
    if CreditNote.objects.filter(customer_return=customer_return).exists():
        raise CreditNoteError('This return already has a credit note.')
    invoice = Invoice.objects.select_for_update().filter(order_id=customer_return.order_id,
                                                         status=Invoice.ISSUED).first()
    if invoice is None:
        raise CreditNoteError("The order's invoice is not issued yet: it will bill only what was kept.")
    if customer_return.kind == CustomerReturn.DOOR and invoice.issued_at > customer_return.created_at:
        raise CreditNoteError('This was refused before the invoice was issued, so it was never billed.')
    rows = _lines_for(None, customer_return, invoice, issued_only=False)
    credit_note = CreditNote(
        company_id=invoice.company_id, customer_return=customer_return, invoice=invoice, created_by=user,
        seller_name=invoice.seller_name, seller_address=invoice.seller_address,
        seller_vat_number=invoice.seller_vat_number, customer_code=invoice.customer_code,
        customer_name=invoice.customer_name, customer_vat_number=invoice.customer_vat_number,
        customer_ref=invoice.customer_ref, address_line1=invoice.address_line1, address_line2=invoice.address_line2,
        address_town=invoice.address_town, address_postcode=invoice.address_postcode,
    )
    _totals(credit_note, rows)
    credit_note.save()
    for row in rows:
        row.credit_note = credit_note
    CreditNoteLine.objects.bulk_create(rows)
    return credit_note


@transaction.atomic
def issue_credit_note(credit_note, user):
    company = Company.objects.select_for_update().get(pk=credit_note.company_id)  # one number at a time
    credit_note = CreditNote.objects.select_for_update().get(pk=credit_note.pk)
    if credit_note.status != CreditNote.DRAFT:
        raise CreditNoteError('This credit note is already issued.')
    Invoice.objects.select_for_update().get(pk=credit_note.invoice_id)  # credits of one invoice take turns
    for row in credit_note.lines.select_related('invoice_line'):
        done_units, done_kg, done_net, done_vat = _credited(row.invoice_line, issued_only=True)
        line = row.invoice_line
        over = ((line.weight_kg is not None and done_kg + (row.weight_kg or 0) > line.weight_kg)
                or (line.quantity is not None and done_units + (row.quantity or 0) > line.quantity)
                or done_net + row.net > line.net or done_vat + row.vat > line.vat)
        if over:
            raise CreditNoteError(f'{line.description} has been credited meanwhile; this would credit more than '
                                  'was invoiced. Delete this draft and make it again.')
    last = CreditNote.objects.filter(company=company, number__isnull=False).aggregate(last=Max('number'))['last']
    credit_note.number, credit_note.status = (last or 0) + 1, CreditNote.ISSUED
    credit_note.credit_date, credit_note.issued_by, credit_note.issued_at = uk_date(), user, timezone.now()
    credit_note.email_status = Invoice.QUEUED
    credit_note.save()
    pk = credit_note.pk
    transaction.on_commit(lambda: _queue_email(pk))
    return credit_note


@transaction.atomic
def discard_draft(credit_note):
    locked = CreditNote.objects.select_for_update().filter(pk=credit_note.pk).first()
    if locked is None or locked.status != CreditNote.DRAFT:
        raise CreditNoteError('An issued credit note is kept.')
    locked.delete()


def _queue_email(credit_note_id):
    try:
        send_credit_note_email.apply_async((credit_note_id,), retry=False)
    except Exception:  # noqa: BLE001 - issued already; staff can resend
        logger.warning('Could not queue the email for credit note %s', credit_note_id, exc_info=True)


@shared_task
def send_credit_note_email(credit_note_id):
    """Email an issued credit note's PDF; records the outcome, never raises, never logs the address."""
    from .invoice_pdf import build_credit_note_pdf

    note = CreditNote.objects.select_related('invoice__order__customer').filter(pk=credit_note_id).first()
    if note is None or note.status != CreditNote.ISSUED:
        return
    to = recipient(note.invoice.order.customer)
    if not to:
        _outcome(note, Invoice.NO_ADDRESS, '')
        return
    message = EmailMessage(
        subject=f'Credit note {note.number_display} from {note.seller_name}',
        body=(f'Please find attached credit note {note.number_display} against invoice '
              f'{note.invoice.number_display}, {note.gross_total} GBP.\n\n{note.seller_name}'),
        from_email=settings.DEFAULT_FROM_EMAIL, to=[to],
    )
    try:
        message.attach(f'{note.number_display}.pdf', build_credit_note_pdf(note), 'application/pdf')
        message.send(fail_silently=False)
    except Exception as error:  # noqa: BLE001 - no traceback: SMTP errors quote the address
        logger.warning('Could not email credit note %s (%s)', note.number_display, type(error).__name__)
        _outcome(note, Invoice.FAILED, to)
        return
    _outcome(note, Invoice.SENT, to)


def _outcome(note, status, to):
    note.email_status, note.emailed_to = status, to
    note.emailed_at = timezone.now() if status == Invoice.SENT else note.emailed_at
    note.save(update_fields=['email_status', 'emailed_to', 'emailed_at'])


def resend(credit_note):
    if credit_note.status != CreditNote.ISSUED:
        raise CreditNoteError('Only an issued credit note is emailed.')
    CreditNote.objects.filter(pk=credit_note.pk).update(email_status=Invoice.QUEUED)
    transaction.on_commit(lambda: _queue_email(credit_note.pk))
