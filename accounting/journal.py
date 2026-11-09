"""
Posting to the journal (G52): the only writer of journals and periods. A journal has at least two lines, each a
debit or a credit to the penny, and its debits equal its credits; every account is the company's own and active.
Nothing is posted into a locked month (a month is locked with every month before it). Posting, locking and
reopening lock the company row first, so a posting never slips into a month being locked, and numbers never clash.
"""

from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import F, Max, Min, Sum
from django.utils import timezone

from tenants.models import Company
from vat.services import uk_date

from .models import Account, Journal, JournalLine, Period, PeriodEvent

ZERO = Decimal('0.00')
PENNY = Decimal('0.01')
MAX_AMOUNT = Decimal('999999999999.99')


class JournalError(ValueError):
    """Refused; ``detail`` is for the API."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def _lock_company(company):
    Company.objects.select_for_update().filter(pk=company.pk).first()


def _amount(value, side):
    if value is None:
        return ZERO
    if not isinstance(value, Decimal):
        raise JournalError(f'The {side} must be a decimal number.')
    try:
        if not value.is_finite() or value != value.quantize(PENNY):
            raise JournalError(f'The {side} is in pounds and pence (2 decimal places).')
    except InvalidOperation:
        raise JournalError(f'The {side} is too large.') from None
    if value < 0:
        raise JournalError(f'A {side} is never negative: use the other side.')
    if value > MAX_AMOUNT:
        raise JournalError(f'A {side} is at most £{MAX_AMOUNT:,}.')
    return value


def _clean_lines(company, lines, allow_inactive=False):
    if len(lines) < 2:
        raise JournalError('A journal has at least two lines.')
    ids = {getattr(line.get('account'), 'pk', line.get('account')) for line in lines}
    accounts = {account.pk: account for account in Account.objects.filter(pk__in=ids, company=company)}
    cleaned = []
    for line in lines:
        account = accounts.get(getattr(line.get('account'), 'pk', line.get('account')))
        if account is None:
            raise JournalError('Every line needs one of your accounts.')
        if not account.is_active and not allow_inactive:
            raise JournalError(f'Account {account.code} is not active.')
        debit, credit = _amount(line.get('debit'), 'debit'), _amount(line.get('credit'), 'credit')
        if (debit > 0) == (credit > 0):
            raise JournalError(f'Each line is a debit or a credit (account {account.code}).')
        description = (line.get('description') or '').strip()
        if len(description) > 120:
            raise JournalError('A line description is at most 120 characters.')
        cleaned.append((account, debit, credit, description))
    debits, credits = sum(row[1] for row in cleaned), sum(row[2] for row in cleaned)
    if debits != credits:
        raise JournalError(f'Debits (£{debits}) and credits (£{credits}) must be equal.')
    return cleaned


def _month(day):
    return day.replace(day=1)


def locked_through(company):
    """The latest locked month (its 1st), or None: nothing is posted on or before its last day."""
    return Period.objects.filter(company=company, locked=True).aggregate(last=Max('start'))['last']


def _check_open(company, day):
    last = locked_through(company)
    if last is not None and _month(day) <= last:
        raise JournalError(f'{day:%B %Y} is locked (up to {last:%B %Y}); reopen it or post in an open month.')


def _signature(rows):
    return sorted((account.pk, debit, credit) for account, debit, credit, _description in rows)


def _post(company, day, narrative, cleaned, user, source, source_ref, reverses=None, reason=''):
    _check_open(company, day)
    Period.objects.get_or_create(company=company, start=_month(day))
    number = (Journal.objects.filter(company=company).aggregate(last=Max('number'))['last'] or 0) + 1
    journal = Journal.objects.create(company=company, number=number, date=day, narrative=narrative, source=source,
                                     source_ref=source_ref, reverses=reverses, reason=reason, created_by=user)
    JournalLine.objects.bulk_create([
        JournalLine(journal=journal, account=account, debit=debit, credit=credit, description=description)
        for account, debit, credit, description in cleaned])
    journal.repeated = False
    return journal


def _narrative(text):
    text = (text or '').strip()
    if not text or len(text) > 255:
        raise JournalError('Give a narrative (at most 255 characters).')
    return text


@transaction.atomic
def post_journal(company, *, date, narrative, lines, user=None, source=Journal.MANUAL, source_ref=None):
    """Post a balanced journal dated ``date`` (UK day). ``lines``: [{account, debit, credit, description}] (Decimals;
    one side each). With a ``source_ref`` it is posted once per company and source: the same lines again return
    the first journal (``repeated``); different lines are refused."""
    if not isinstance(date, date_type):
        raise JournalError('The date must be a date.')
    narrative = _narrative(narrative)
    _lock_company(company)
    cleaned = _clean_lines(company, lines)
    if source_ref is not None:
        earlier = Journal.objects.filter(company=company, source=source, source_ref=source_ref).first()
        if earlier is not None:
            mine = [(line.account, line.debit, line.credit, '') for line in earlier.lines.select_related('account')]
            if _signature(mine) != _signature(cleaned):
                raise JournalError(f'{source} {source_ref} was posted already, with different lines.')
            earlier.repeated = True
            return earlier
    return _post(company, date, narrative, cleaned, user, source, source_ref)


@transaction.atomic
def reverse_journal(journal, user, reason, date=None):
    """Post the opposite of ``journal`` (every line's sides swapped) on ``date`` (default today, UK), once."""
    reason = (reason or '').strip()
    if not reason or len(reason) > 255:
        raise JournalError('Give a reason (at most 255 characters).')
    _lock_company(journal.company)
    journal = Journal.objects.get(pk=journal.pk)
    if journal.reverses_id is not None:
        raise JournalError('A reversal is not reversed again; post the journal afresh.')
    if Journal.objects.filter(reverses=journal).exists():
        raise JournalError(f'Journal {journal.number} is reversed already.')
    lines = [{'account': line.account_id, 'debit': line.credit or None, 'credit': line.debit or None,
              'description': line.description} for line in journal.lines.all()]
    cleaned = _clean_lines(journal.company, lines, allow_inactive=True)  # undoing stays possible
    narrative = f'Reversal of journal {journal.number}: {journal.narrative}'[:255]
    return _post(journal.company, date or uk_date(), narrative, cleaned, user, 'reversal', None, reverses=journal,
                 reason=reason)


def _first_of_month(month):
    if not isinstance(month, date_type) or month.day != 1:
        raise JournalError('A month is given by its first day.')
    return month


def _months(first, last):
    while first <= last:
        yield first
        first = _next_month(first)


def _next_month(day):
    return date_type(day.year + (day.month == 12), day.month % 12 + 1, 1)


@transaction.atomic
def lock_period(company, month, user):
    """Lock ``month`` and every month before it (from the first with a journal or a period)."""
    month = _first_of_month(month)
    _lock_company(company)
    earliest = [month,
                Journal.objects.filter(company=company).aggregate(first=Min('date'))['first'],
                Period.objects.filter(company=company).aggregate(first=Min('start'))['first']]
    first = _month(min(day for day in earliest if day is not None))
    now = timezone.now()
    for start in _months(first, month):
        period, _created = Period.objects.get_or_create(company=company, start=start)
        if not period.locked:
            period.locked, period.locked_at, period.locked_by = True, now, user
            period.save(update_fields=['locked', 'locked_at', 'locked_by'])
            PeriodEvent.objects.create(period=period, action=PeriodEvent.LOCK, user=user)
    return Period.objects.get(company=company, start=month)


@transaction.atomic
def reopen_period(company, month, user, reason):
    """Reopen a locked ``month`` and every locked month after it, with a reason."""
    month = _first_of_month(month)
    reason = (reason or '').strip()
    if not reason or len(reason) > 255:
        raise JournalError('Give a reason (at most 255 characters).')
    _lock_company(company)
    if not Period.objects.filter(company=company, start=month, locked=True).exists():
        raise JournalError(f'{month:%B %Y} is not locked.')
    for period in Period.objects.filter(company=company, start__gte=month, locked=True):
        period.locked, period.locked_at, period.locked_by = False, None, None
        period.save(update_fields=['locked', 'locked_at', 'locked_by'])
        PeriodEvent.objects.create(period=period, action=PeriodEvent.REOPEN, reason=reason, user=user)
    return Period.objects.get(company=company, start=month)


def account_balance(account, on=None):
    """Debits less credits on ``account`` (to the end of ``on``): positive for assets and expenses."""
    lines = JournalLine.objects.filter(account=account)
    if on is not None:
        lines = lines.filter(journal__date__lte=on)
    return (lines.aggregate(total=Sum(F('debit') - F('credit')))['total'] or ZERO).quantize(PENNY)
