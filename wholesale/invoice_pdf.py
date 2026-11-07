"""
The invoice PDF (G43): seller and customer with both VAT numbers, number and dates, the lines, the VAT summary by
rate and the totals. A draft carries no number and is marked DRAFT. Built only from the invoice's own (frozen)
fields, so it reads the same however the customer or company change later.
"""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Invoice


def _amount(line):
    if line.weight_kg is not None and line.quantity:
        return f'{line.quantity} pcs, {line.weight_kg} kg'
    if line.weight_kg is not None:
        return f'{line.weight_kg} kg'
    return str(line.quantity)


def _lines_html(text):
    return '<br/>'.join(escape(part) for part in text.splitlines() if part.strip())


def build_invoice_pdf(invoice):
    """``invoice`` as PDF bytes. Drafts take seller and customer from the order as it is (and say DRAFT)."""
    if invoice.status == Invoice.DRAFT:
        order, company = invoice.order, invoice.company
        customer = order.customer
        seller = (company.name, company.address, company.vat_number)
        buyer = (customer.account_code, customer.legal_name, customer.vat_number, order.customer_ref,
                 [order.line1, order.line2, order.town, order.postcode])
        title, dates = 'DRAFT invoice', 'Not issued'
    else:
        seller = (invoice.seller_name, invoice.seller_address, invoice.seller_vat_number)
        buyer = (invoice.customer_code, invoice.customer_name, invoice.customer_vat_number, invoice.customer_ref,
                 [invoice.address_line1, invoice.address_line2, invoice.address_town, invoice.address_postcode])
        title = f'Invoice {invoice.number_display}'
        dates = f'Invoice date {invoice.invoice_date:%d/%m/%Y} · Due {invoice.due_date:%d/%m/%Y}'
    seller_name, seller_address, seller_vat = seller
    code, name, buyer_vat, reference, address = buyer

    styles = getSampleStyleSheet()
    small = styles['BodyText'].clone('small', fontSize=8, leading=10)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=15 * mm, title=title, author=seller_name, invariant=True)
    parties = Table([[
        Paragraph(f'<b>{escape(seller_name)}</b><br/>{_lines_html(seller_address)}<br/>'
                  f'VAT number {escape(seller_vat or "not set")}', small),
        Paragraph(f'<b>Invoice to</b><br/>{escape(name)} ({escape(code)})<br/>'
                  f'{"<br/>".join(escape(part) for part in address if part)}<br/>'
                  f'VAT number {escape(buyer_vat or "none")}', small),
    ]], colWidths=[90 * mm, 90 * mm])
    parties.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story = [
        Paragraph(escape(title), styles['Title']),
        Paragraph(f'{escape(dates)} · Order {escape(invoice.order.number_display)}'
                  f'{f" · Your ref {escape(reference)}" if reference else ""}', styles['Normal']),
        Spacer(1, 4 * mm), parties, Spacer(1, 6 * mm),
    ]
    rows = [['Description', 'Qty / kg', 'Unit price', 'VAT %', 'Net', 'VAT']]
    rows += [[Paragraph(escape(line.description), small), _amount(line), f'{line.unit_price}', f'{line.vat_rate}',
              f'{line.net}', f'{line.vat}'] for line in invoice.lines.order_by('id')]
    table = Table(rows, colWidths=[64 * mm, 30 * mm, 24 * mm, 16 * mm, 24 * mm, 22 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story += [table, Spacer(1, 4 * mm), Paragraph('VAT summary', styles['Heading4'])]
    summary = [['Rate', 'Net', 'VAT']] + [[f'{row["rate"]}%', row['net'], row['vat']] for row in invoice.vat_summary]
    summary += [['Total', f'{invoice.net_total}', f'{invoice.vat_total}']]
    summary_table = Table(summary, colWidths=[30 * mm, 30 * mm, 30 * mm], hAlign='RIGHT')
    summary_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey), ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
    ]))
    story += [summary_table, Spacer(1, 4 * mm),
              Paragraph(f'<b>Amount due: GBP {invoice.gross_total}</b>', styles['Heading3'])]

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.drawString(15 * mm, 8 * mm, f'{title} · page {document.page}')
        if invoice.status == Invoice.DRAFT:
            canvas.setFont('Helvetica-Bold', 60)
            canvas.setFillColor(colors.Color(0.8, 0.8, 0.8))
            canvas.drawCentredString(105 * mm, 150 * mm, 'DRAFT')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
