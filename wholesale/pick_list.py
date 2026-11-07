"""
The printable pick list for a trade order (G42): one table per zone — ambient, chilled, frozen, then butchery — with
what was ordered, the lots reserved for it (expiry and amount) and empty boxes for the picked count and kg.
"""

from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from inventory.models import Product, StockReservation

from .models import SalesOrderLine

ZONE_ORDER = [Product.AMBIENT, Product.CHILLED, Product.FROZEN, Product.BUTCHERY_ZONE]
ZONE_NAMES = dict(Product.ZONES)


def _ordered(line):
    product = line.product
    if product.is_weighed and not product.is_catch_weight:
        return f'{line.weight_kg} kg'
    if product.is_catch_weight:
        return f'{line.quantity} pcs (~{line.weight_kg} kg)' if line.weight_kg else f'{line.quantity} pcs'
    return str(line.quantity)


def _lots(line):
    rows = (StockReservation.objects.filter(order_line=line, released_at__isnull=True).select_related('batch')
            .order_by('batch__expiration_date', 'batch_id'))
    loose = line.product.is_weighed and not line.product.is_catch_weight
    return '<br/>'.join(
        f'{escape(row.batch.batch_number)} · {row.batch.expiration_date:%d/%m/%y} · '
        f'{f"{row.weight_kg} kg" if loose else row.quantity}'
        for row in rows
    ) or 'none reserved'


def build_pick_list(order):
    """The pick list for ``order`` as PDF bytes."""
    styles = getSampleStyleSheet()
    small = styles['BodyText'].clone('small', fontSize=8, leading=10)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=15 * mm, title=f'Pick list {order.number_display}')
    customer = order.customer
    address = ', '.join(part for part in (order.line1, order.line2, order.town, order.postcode) if part)
    story = [
        Paragraph(f'Pick list {order.number_display}', styles['Title']),
        Paragraph(escape(f'{order.company.name} · {customer.account_code} '
                         f'{customer.trading_name or customer.legal_name}'), styles['Normal']),
        Paragraph(f'Delivery {order.delivery_date:%a %d %b %Y} · {escape(address)}', styles['Normal']),
    ]
    if order.instructions:
        story.append(Paragraph(f'Driver: {escape(order.instructions)}', styles['Normal']))
    lines = [line for line in order.lines.select_related('product')
             if line.substitution not in (SalesOrderLine.PROPOSED, SalesOrderLine.REJECTED)]
    for zone in ZONE_ORDER:
        in_zone = sorted((line for line in lines if line.product.pick_zone == zone),
                         key=lambda line: (line.product.name.lower(), line.pk))
        if not in_zone:
            continue
        story += [Spacer(1, 6 * mm), Paragraph(ZONE_NAMES[zone], styles['Heading2'])]
        rows = [['Product', 'Ordered', 'Reserved lots (lot · expiry · amount)', 'Picked', 'Kg']]
        rows += [[Paragraph(escape(line.product.name), small), _ordered(line), Paragraph(_lots(line), small), '', '']
                 for line in in_zone]
        table = Table(rows, colWidths=[55 * mm, 28 * mm, 62 * mm, 18 * mm, 17 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
        ]))
        story.append(table)
    printed = timezone.localtime().strftime('%d/%m/%Y %H:%M')

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.drawString(15 * mm, 8 * mm, f'{order.number_display} · page {document.page} · printed {printed}')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
