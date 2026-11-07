"""
Trade invoices (G43). A draft is built from a picked order's lines at what was picked — units, pieces and the real
kg for weighed goods — at the price and VAT rate fixed on each order line (G40/G42). Net and VAT are rounded half
up to the penny per line; totals and the VAT summary by rate are sums of the lines.

Issuing takes the company's next number (highest issued + 1, under the company row lock: drafts take none and an
issued invoice is never deleted, so there are no gaps), copies in seller and customer with both VAT numbers, and
freezes the invoice. The PDF is emailed after commit by ``send_invoice_email``; a failure is recorded on the
invoice, never raised.
"""

import logging
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from tenants.models import Company
from vat.services import HUNDRED, PENNY, uk_date

from .models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine, TradeCustomer, TradeCustomerContact

logger = logging.getLogger(__name__)
S = SalesOrder
INVOICEABLE = (S.PICKED, S.DISPATCHED, S.DELIVERED)


class InvoiceError(ValueError):
    """The invoice can't be made or issued; the message is for the API."""


def _round(value):
    return value.quantize(PENNY, rounding=ROUND_HALF_UP)


def _billed_lines(order):
    """``InvoiceLine`` objects (unsaved) for what was picked; lines with nothing picked are left out."""
    rows = []
    lines = order.lines.select_related('product').exclude(
        substitution__in=[SalesOrderLine.PROPOSED, SalesOrderLine.REJECTED]).order_by('id')
    for line in lines:
        product = line.product
        if product.is_weighed:
            amount = line.picked_weight_kg or Decimal('0')
            quantity = line.picked_quantity if product.is_catch_weight else None
            weight = amount
        else:
            amount = Decimal(line.picked_quantity or 0)
            quantity, weight = line.picked_quantity, None
        if amount <= 0:
            continue
        if line.unit_price is None or line.vat_rate is None:
            raise InvoiceError(f'{product.name} has no price; the order was not confirmed properly.')
        net = _round(amount * line.unit_price)
        vat = _round(net * line.vat_rate / HUNDRED)
        rows.append(InvoiceLine(
            order_line=line, product=product, description=product.name[:255], quantity=quantity, weight_kg=weight,
            unit_price=line.unit_price, vat_code=line.vat_code, vat_rate=line.vat_rate, net=net, vat=vat,
            gross=net + vat,
        ))
    if not rows:
        raise InvoiceError('Nothing was picked on this order, so there is nothing to invoice.')
    return rows


def _totals(invoice, rows):
    by_rate = defaultdict(lambda: [Decimal('0'), Decimal('0')])
    for row in rows:
        by_rate[row.vat_rate][0] += row.net
        by_rate[row.vat_rate][1] += row.vat
    invoice.net_total = sum((row.net for row in rows), Decimal('0'))
    invoice.vat_total = sum((row.vat for row in rows), Decimal('0'))
    invoice.gross_total = invoice.net_total + invoice.vat_total
    invoice.vat_summary = [{'rate': str(rate), 'net': str(net), 'vat': str(vat)}
                           for rate, (net, vat) in sorted(by_rate.items())]


def _write_lines(invoice, rows):
    invoice.lines.all().delete()
    for row in rows:
        row.invoice = invoice
    InvoiceLine.objects.bulk_create(rows)
    _totals(invoice, rows)


@transaction.atomic
def create_invoice(order, user):
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in INVOICEABLE:
        raise InvoiceError(f'A {order.get_status_display().lower()} order is not invoiced; pick it first.')
    if Invoice.objects.filter(order=order).exists():
        raise InvoiceError('This order already has an invoice.')
    rows = _billed_lines(order)
    invoice = Invoice.objects.create(company_id=order.company_id, order=order, created_by=user)
    _write_lines(invoice, rows)
    invoice.save()
    return invoice


@transaction.atomic
def refresh_draft(invoice):
    """Rebuild a draft's lines and totals from the order as it is now."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Invoice.DRAFT:
        raise InvoiceError('Only a draft invoice is rebuilt.')
    _write_lines(invoice, _billed_lines(invoice.order))
    invoice.save()
    return invoice


@transaction.atomic
def issue(invoice, user):
    company = Company.objects.select_for_update().get(pk=invoice.company_id)  # one number at a time per company
    invoice = Invoice.objects.select_for_update().select_related('order__customer').get(pk=invoice.pk)
    if invoice.status != Invoice.DRAFT:
        raise InvoiceError('This invoice is already issued.')
    if not company.vat_number:
        raise InvoiceError("Set your company's VAT number before issuing invoices.")
    if not invoice.lines.exists():
        raise InvoiceError('An invoice needs at least one line.')
    order = invoice.order
    customer = TradeCustomer.objects.get(pk=order.customer_id)
    last = Invoice.objects.filter(company=company, number__isnull=False).aggregate(last=Max('number'))['last']
    invoice.number = (last or 0) + 1
    invoice.status = Invoice.ISSUED
    invoice.invoice_date = uk_date()
    invoice.due_date = invoice.invoice_date + timedelta(days=customer.payment_terms_days)
    invoice.seller_name, invoice.seller_address, invoice.seller_vat_number = (
        company.name, company.address, company.vat_number)
    invoice.customer_code, invoice.customer_name = customer.account_code, customer.legal_name
    invoice.customer_vat_number, invoice.customer_ref = customer.vat_number, order.customer_ref
    invoice.address_line1, invoice.address_line2 = order.line1, order.line2
    invoice.address_town, invoice.address_postcode = order.town, order.postcode
    invoice.issued_by, invoice.issued_at = user, timezone.now()
    invoice.email_status = Invoice.QUEUED
    invoice.save()
    if order.status == S.DELIVERED:
        from .orders import advance  # delivered -> invoiced (G44 does the same when a delivery completes)

        advance(order, S.INVOICED)
    pk = invoice.pk
    transaction.on_commit(lambda: _queue_email(pk))
    return invoice


def _queue_email(invoice_id):
    # The invoice is issued already: a broker problem must not turn that into an error. It shows as "queued";
    # staff can resend.
    try:
        send_invoice_email.apply_async((invoice_id,), retry=False)
    except Exception:  # noqa: BLE001
        logger.warning('Could not queue the email for invoice %s', invoice_id, exc_info=True)


def recipient(customer):
    if customer.invoice_email:
        return customer.invoice_email
    contact = TradeCustomerContact.objects.filter(customer=customer, is_primary=True).exclude(email='').first()
    return contact.email if contact else ''


@shared_task
def send_invoice_email(invoice_id):
    """Email an issued invoice's PDF to the customer; records the outcome on the invoice, never raises."""
    from .invoice_pdf import build_invoice_pdf

    invoice = Invoice.objects.select_related('order__customer').filter(pk=invoice_id).first()
    if invoice is None or invoice.status != Invoice.ISSUED:
        return
    to = recipient(invoice.order.customer)
    if not to:
        _email_outcome(invoice, Invoice.NO_ADDRESS, '')
        return
    message = EmailMessage(
        subject=f'Invoice {invoice.number_display} from {invoice.seller_name}',
        body=(f'Please find attached invoice {invoice.number_display} for order {invoice.order.number_display}, '
              f'{invoice.gross_total} GBP, due on {invoice.due_date:%d %B %Y}.\n\n{invoice.seller_name}'),
        from_email=settings.DEFAULT_FROM_EMAIL, to=[to],
    )
    message.attach(f'{invoice.number_display}.pdf', build_invoice_pdf(invoice), 'application/pdf')
    try:
        message.send(fail_silently=False)
    except Exception:  # noqa: BLE001 - SMTP errors are many and varied; the address is not logged
        logger.exception('Could not email invoice %s', invoice.number_display)
        _email_outcome(invoice, Invoice.FAILED, to)
        return
    _email_outcome(invoice, Invoice.SENT, to)


def _email_outcome(invoice, status, to):
    invoice.email_status, invoice.emailed_to = status, to
    invoice.emailed_at = timezone.now() if status == Invoice.SENT else invoice.emailed_at
    invoice.save(update_fields=['email_status', 'emailed_to', 'emailed_at'])


def resend(invoice):
    if invoice.status != Invoice.ISSUED:
        raise InvoiceError('Only an issued invoice is emailed.')
    Invoice.objects.filter(pk=invoice.pk).update(email_status=Invoice.QUEUED)
    transaction.on_commit(lambda: _queue_email(invoice.pk))
