"""Reports from the journal (G53 trial balance; G54 builds the rest on it)."""

from decimal import Decimal

from django.db.models import Sum

from .models import JournalLine

ZERO = Decimal('0.00')


def trial_balance(company, on=None):
    """Every account with something posted (to the end of ``on``): its debits and credits; and the two totals, which
    are always equal."""
    lines = JournalLine.objects.filter(journal__company=company)
    if on is not None:
        lines = lines.filter(journal__date__lte=on)
    rows = [
        {'account': row['account'], 'code': row['account__code'], 'name': row['account__name'],
         'type': row['account__type'], 'debit': row['debit'] or ZERO, 'credit': row['credit'] or ZERO}
        for row in lines.values('account', 'account__code', 'account__name', 'account__type')
        .annotate(debit=Sum('debit'), credit=Sum('credit')).order_by('account__code')
    ]
    return {'on': on, 'rows': rows, 'debit': sum((row['debit'] for row in rows), ZERO),
            'credit': sum((row['credit'] for row in rows), ZERO)}
