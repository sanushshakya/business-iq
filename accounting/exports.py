"""
The accountant's exports (G55), CSV: every journal line for a period, and the journal lines behind VAT boxes 1, 4, 6
and 7 (the same queries as the return, so their totals are the return's, boxes 6 and 7 before the pence are dropped).
Text cells never start a formula in a spreadsheet.
"""

import csv
from decimal import Decimal

from django.http import HttpResponse

from common.csv_safe import cell

from .models import JournalLine
from .vat_return import BOX_SIGNS, box_lines

ZERO = Decimal('0.00')
JOURNAL_HEADINGS = ['Journal', 'Date', 'Source', 'Source ref', 'Narrative', 'Account', 'Account name', 'Debit',
                    'Credit', 'Line description']
VAT_HEADINGS = ['Box', 'Journal', 'Date', 'Source', 'Source ref', 'Narrative', 'Account', 'Account name', 'Amount']


def _response(name, start, end):
    answer = HttpResponse(content_type='text/csv; charset=utf-8')
    answer['Content-Disposition'] = f'attachment; filename="{name}-{start.isoformat()}-to-{end.isoformat()}.csv"'
    return answer, csv.writer(answer)


def _lines(queryset):
    return queryset.select_related('journal', 'account').order_by('journal__date', 'journal__number', 'id')


def journals_csv(company, start, end):
    answer, writer = _response('journals', start, end)
    writer.writerow(JOURNAL_HEADINGS)
    debits = credits = ZERO
    for line in _lines(JournalLine.objects.filter(journal__company=company, journal__date__gte=start,
                                                  journal__date__lte=end)):
        journal = line.journal
        writer.writerow([journal.number, journal.date.isoformat(), journal.source, cell(journal.source_ref or ''),
                         cell(journal.narrative), line.account.code, cell(line.account.name), f'{line.debit:.2f}',
                         f'{line.credit:.2f}', cell(line.description)])
        debits += line.debit
        credits += line.credit
    writer.writerow(['Total', '', '', '', '', '', '', f'{debits:.2f}', f'{credits:.2f}', ''])
    return answer


def vat_csv(company, start, end):
    answer, writer = _response('vat', start, end)
    writer.writerow(VAT_HEADINGS)
    boxes = box_lines(company, start, end)
    for box, sign in BOX_SIGNS.items():
        total = ZERO
        for line in _lines(boxes[box]):
            amount = sign * (line.debit - line.credit)
            journal = line.journal
            writer.writerow([box, journal.number, journal.date.isoformat(), journal.source,
                             cell(journal.source_ref or ''), cell(journal.narrative), line.account.code,
                             cell(line.account.name), f'{amount:.2f}'])
            total += amount
        writer.writerow([box, 'Total', '', '', '', '', '', '', f'{total:.2f}'])
    return answer
