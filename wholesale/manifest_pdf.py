"""
The delivery run manifest (G44): the run, van and driver, then each stop in driving order with the customer, address,
driver's instructions, what was picked for it, and boxes for who received it, the time and a signature.
"""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import SalesOrderLine


def _picked(line):
    product = line.product
    if product.is_weighed and not product.is_catch_weight:
        return f'{line.picked_weight_kg} kg'
    if product.is_catch_weight:
        return f'{line.picked_quantity} pcs, {line.picked_weight_kg} kg'
    return str(line.picked_quantity)


def build_manifest_pdf(run):
    """``run`` as a printable manifest (PDF bytes)."""
    styles = getSampleStyleSheet()
    small = styles['BodyText'].clone('small', fontSize=8, leading=10)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=15 * mm, title=f'Manifest {run.number_display}')
    story = [
        Paragraph(f'Manifest {run.number_display}', styles['Title']),
        Paragraph(escape(f'{run.run_date:%a %d %b %Y} · Van {run.vehicle.code} {run.vehicle.name} · '
                         f'Driver {run.driver.name or "(no name)"}'), styles['Normal']),
    ]
    stops = run.stops.select_related('order__customer').order_by('position')
    for stop in stops:
        order, customer = stop.order, stop.order.customer
        address = ', '.join(part for part in (order.line1, order.line2, order.town, order.postcode) if part)
        block = [
            Spacer(1, 5 * mm),
            Paragraph(escape(f'{stop.position}. {order.number_display} · {customer.account_code} '
                             f'{customer.trading_name or customer.legal_name}'), styles['Heading3']),
            Paragraph(escape(address), styles['Normal']),
        ]
        if order.instructions:
            block.append(Paragraph(f'Instructions: {escape(order.instructions)}', styles['Normal']))
        lines = order.lines.select_related('product').exclude(
            substitution__in=[SalesOrderLine.PROPOSED, SalesOrderLine.REJECTED]).order_by('id')
        rows = [['Product', 'Picked']] + [[Paragraph(escape(line.product.name), small), _picked(line)]
                                          for line in lines if (line.picked_quantity or line.picked_weight_kg)]
        goods = Table(rows, colWidths=[120 * mm, 60 * mm], repeatRows=1)
        goods.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey), ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ]))
        sign = Table([['Received by', '', 'Time', '', 'Signature', '']],
                     colWidths=[22 * mm, 48 * mm, 14 * mm, 26 * mm, 20 * mm, 50 * mm], rowHeights=[12 * mm])
        sign.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.5, colors.grey), ('FONTSIZE', (0, 0), (-1, -1), 8),
                                  ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
        block += [Spacer(1, 2 * mm), goods, Spacer(1, 2 * mm), sign]
        story.append(KeepTogether(block))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.drawString(15 * mm, 8 * mm, f'{run.number_display} · page {document.page}')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
