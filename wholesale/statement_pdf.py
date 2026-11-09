"""The statement PDF (G51): the month's entries with the running balance, what is still to pay, and its ageing."""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .aged_debtors import due_date
from .invoice_pdf import _lines_html

AGEING = [('current', 'Current'), ('days_1_30', '1-30 days'), ('days_31_60', '31-60 days'),
          ('days_61_90', '61-90 days'), ('days_90_plus', '90+ days'), ('on_account', 'On account'),
          ('total', 'Balance')]
GRID = [('GRID', (0, 0), (-1, -1), 0.5, colors.grey), ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('FONTSIZE', (0, 0), (-1, -1), 8), ('VALIGN', (0, 0), (-1, -1), 'TOP')]


def _table(rows, widths, right_from=1):
    table = Table(rows, colWidths=[width * mm for width in widths], repeatRows=1)
    table.setStyle(TableStyle(GRID + [('ALIGN', (right_from, 1), (-1, -1), 'RIGHT')]))
    return table


def build_statement_pdf(data):
    """``data`` from ``statements.statement_data`` as PDF bytes."""
    customer, company = data['customer'], data['company']
    styles = getSampleStyleSheet()
    small = styles['BodyText'].clone('small', fontSize=8, leading=10)
    title = f'Statement {data["start"]:%B %Y}'
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=15 * mm, title=title, author=company.name, invariant=True)
    parties = Table([[
        Paragraph(f'<b>{escape(company.name)}</b><br/>{_lines_html(company.address)}<br/>'
                  f'VAT number {escape(company.vat_number or "not set")}', small),
        Paragraph(f'<b>Statement for</b><br/>{escape(customer.legal_name)} ({escape(customer.account_code)})', small),
    ]], colWidths=[90 * mm, 90 * mm])
    parties.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story = [Paragraph(escape(title), styles['Title']),
             Paragraph(f'{data["start"]:%d/%m/%Y} to {data["end"]:%d/%m/%Y}', styles['Normal']),
             Spacer(1, 4 * mm), parties, Spacer(1, 6 * mm)]

    rows = [['Date', 'Description', 'Debit', 'Credit', 'Balance'],
            ['', 'Opening balance', '', '', f'{data["opening"]}']]
    for entry in data['entries']:
        rows.append([f'{entry.entry_date:%d/%m/%Y}', Paragraph(escape(entry.description), small),
                     f'{entry.amount}' if entry.amount > 0 else '', f'{-entry.amount}' if entry.amount < 0 else '',
                     f'{entry.running}'])
    rows.append(['', 'Closing balance', '', '', f'{data["closing"]}'])
    story += [_table(rows, [22, 88, 24, 24, 24], right_from=2), Spacer(1, 6 * mm)]

    story.append(Paragraph('Still to pay', styles['Heading4']))
    if data['open_items']:
        rows = [['Date', 'Document', 'Due', 'Amount', 'Open']]
        for entry, still in data['open_items']:
            rows.append([f'{entry.entry_date:%d/%m/%Y}', Paragraph(escape(entry.description), small),
                         f'{due_date(entry):%d/%m/%Y}', f'{entry.amount}', f'{still}'])
        story += [_table(rows, [22, 88, 22, 25, 25], right_from=3)]
    else:
        story.append(Paragraph('Nothing.', small))
    story += [Spacer(1, 4 * mm), _table([[label for _key, label in AGEING],
                                         [f'{data["ageing"][key]}' for key, _label in AGEING]],
                                        [26] * len(AGEING), right_from=0)]

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.drawString(15 * mm, 8 * mm, f'{title} · {customer.account_code} · page {document.page}')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
