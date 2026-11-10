"""Reports from the journal: the trial balance (G53), profit and loss and the balance sheet (G54)."""

from decimal import Decimal

from django.db.models import Sum

from .models import Account, JournalLine

ZERO = Decimal('0.00')


def trial_balance(company, on=None):
    """Every account with a balance (to the end of ``on``): its net as a debit or a credit; the totals agree when the
    books balance."""
    lines = JournalLine.objects.filter(journal__company=company)
    if on is not None:
        lines = lines.filter(journal__date__lte=on)
    rows = []
    for row in (lines.values('account', 'account__code', 'account__name', 'account__type')
                .annotate(debit=Sum('debit'), credit=Sum('credit')).order_by('account__code')):
        net = (row['debit'] or ZERO) - (row['credit'] or ZERO)
        if net:
            rows.append({'account': row['account'], 'code': row['account__code'], 'name': row['account__name'],
                         'type': row['account__type'], 'debit': max(net, ZERO), 'credit': max(-net, ZERO)})
    return {'on': on, 'rows': rows, 'debit': sum((row['debit'] for row in rows), ZERO),
            'credit': sum((row['credit'] for row in rows), ZERO)}


def _by_account(lines, types, sign):
    """``[{account, code, name, amount}]`` for accounts of ``types`` with a non-zero amount; ``sign`` 1 = debit −
    credit (assets, expenses), -1 = credit − debit (liabilities, equity, income)."""
    rows = []
    for row in (lines.filter(account__type__in=types)
                .values('account', 'account__code', 'account__name')
                .annotate(debit=Sum('debit'), credit=Sum('credit')).order_by('account__code')):
        amount = sign * ((row['debit'] or ZERO) - (row['credit'] or ZERO))
        if amount:
            rows.append({'account': row['account'], 'code': row['account__code'], 'name': row['account__name'],
                         'amount': amount})
    return rows


def _total(rows):
    return sum((row['amount'] for row in rows), ZERO)


def profit_and_loss(company, start, end):
    """Income and expenses posted from ``start`` to ``end`` (UK days, inclusive), and the net profit."""
    lines = JournalLine.objects.filter(journal__company=company, journal__date__gte=start, journal__date__lte=end)
    income = _by_account(lines, [Account.INCOME], -1)
    expenses = _by_account(lines, [Account.EXPENSE], 1)
    return {'start': start, 'end': end, 'income': income, 'expenses': expenses, 'total_income': _total(income),
            'total_expenses': _total(expenses), 'net_profit': _total(income) - _total(expenses)}


def balance_sheet(company, on):
    """Assets, liabilities and equity at the end of ``on``. There is no year-end close yet, so every profit to that day
    shows in equity as "Profit to date"; assets = liabilities + equity."""
    lines = JournalLine.objects.filter(journal__company=company, journal__date__lte=on)
    assets = _by_account(lines, [Account.ASSET], 1)
    liabilities = _by_account(lines, [Account.LIABILITY], -1)
    equity = _by_account(lines, [Account.EQUITY], -1)
    profit = _total(_by_account(lines, [Account.INCOME], -1)) - _total(_by_account(lines, [Account.EXPENSE], 1))
    if profit:
        equity.append({'account': None, 'code': None, 'name': 'Profit to date', 'amount': profit})
    return {'on': on, 'assets': assets, 'liabilities': liabilities, 'equity': equity,
            'total_assets': _total(assets), 'total_liabilities': _total(liabilities), 'total_equity': _total(equity),
            'total_liabilities_and_equity': _total(liabilities) + _total(equity)}
