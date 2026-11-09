"""
Monthly statements (G51): last month's activity from the ledger (G47) and what was still open at month end with
its ageing (G50), as a PDF emailed on the 1st to every customer that wants one and has something to say. Each
automatic statement is sent once (``unique_automatic_statement``); staff may send any month again by hand.
"""

import calendar
from datetime import timedelta

from celery import shared_task
from django.db import IntegrityError, transaction

from vat.services import uk_date

from . import ledger
from .aged_debtors import COLUMNS, ZERO, open_items, report
from .customer_emails import deliver_customer_email, queue_after_commit  # noqa: F401 - the task, for patching
from .models import CustomerEmail, LedgerEntry, TradeCustomer


def month_of(day):
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


def previous_month(day):
    return month_of(day.replace(day=1) - timedelta(days=1))


def statement_data(customer, start, end):
    """Everything the statement shows, as at the end of ``end``."""
    moves = ledger.statement(customer, start, end)
    rows = report(customer.company_id, end, customer)['customers']
    return {'customer': customer, 'company': customer.company, 'start': start, 'end': end, **moves,
            'open_items': open_items(customer, end), 'ageing': rows[0] if rows else dict.fromkeys(COLUMNS, ZERO)}


def send_statement(customer, start, end, user=None):
    """Log a statement for ``start``–``end`` and send it after commit. ``user`` None is the schedule's own: once
    per customer and month (None if it was sent already)."""
    try:
        with transaction.atomic():
            email = CustomerEmail.objects.create(customer=customer, kind=CustomerEmail.STATEMENT, period_start=start,
                                                 period_end=end, created_by=user)
    except IntegrityError:
        if user is not None:
            raise
        return None
    queue_after_commit(email)
    return email


def _has_something_to_say(customer, start, end):
    return (LedgerEntry.objects.filter(customer=customer, entry_date__gte=start, entry_date__lte=end).exists()
            or ledger.balance(customer, on=end) != 0)


@shared_task
def run_monthly_statements(today=None):
    """On the 1st (beat): last month's statement for every customer not closed that takes them by email and had
    activity or a balance."""
    start, end = previous_month(today or uk_date())
    customers = (TradeCustomer.objects.exclude(status=TradeCustomer.CLOSED).filter(statements_by_email=True)
                 .select_related('company').order_by('company_id', 'account_code'))
    sent = 0
    for customer in customers:
        if _has_something_to_say(customer, start, end):
            with transaction.atomic():
                sent += send_statement(customer, start, end) is not None
    return sent


def statement_message(email):
    from .statement_pdf import build_statement_pdf

    customer = email.customer
    data = statement_data(customer, email.period_start, email.period_end)
    month = f'{email.period_start:%B %Y}'
    body = (f'Please find attached your statement for {month}. Balance at {email.period_end:%d %B %Y}: '
            f'{data["closing"]} GBP.\n\n{customer.company.name}')
    name = f'statement-{customer.account_code}-{email.period_start:%Y-%m}.pdf'
    return (f'Statement for {month} from {customer.company.name}', body,
            (name, build_statement_pdf(data), 'application/pdf'))
