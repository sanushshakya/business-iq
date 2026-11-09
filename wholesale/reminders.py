"""
Payment reminders (G51): every day (beat) each customer that takes them gets at most one email listing the invoices
that reached a stage — 3 days before due, 7 and 14 days overdue — with what is still open (G49 allocations).
``ReminderItem`` is unique per invoice and stage, so a stage is never sent twice, whatever runs at once. A stage
missed for a few days (beat or worker down) is caught up for up to 6 days, never after a later one.
"""


from celery import shared_task
from django.db import IntegrityError, transaction

from vat.services import uk_date

from .customer_emails import logger, queue_after_commit
from .models import CustomerEmail, LedgerEntry, ReminderItem, TradeCustomer
from .payments import open_debits

# (stage, days from the due date); a later stage outranks an earlier one.
STAGES = [(ReminderItem.BEFORE_DUE, -3), (ReminderItem.OVERDUE_7, 7), (ReminderItem.OVERDUE_14, 14)]
CATCH_UP_DAYS = 6
LABELS = dict(ReminderItem.STAGES)


def due_stage(due_date, today, recorded):
    """The stage to send for an invoice due ``due_date`` on ``today``, given the stages already ``recorded``."""
    late = (today - due_date).days
    for index in range(len(STAGES) - 1, -1, -1):
        stage, offset = STAGES[index]
        if not offset <= late <= offset + CATCH_UP_DAYS:
            continue
        if stage == ReminderItem.BEFORE_DUE and late >= 0:
            return None
        if any(name in recorded for name, _offset in STAGES[index:]):
            return None
        return stage
    return None


def _recorded(invoice_ids):
    found = {}
    for invoice_id, stage in ReminderItem.objects.filter(invoice_id__in=invoice_ids).values_list('invoice_id',
                                                                                              'stage'):
        found.setdefault(invoice_id, set()).add(stage)
    return found


class _NothingToSend(Exception):
    pass


def remind(customer, today):
    """Log and (after commit) send this customer's reminder for ``today``; None when nothing reached a stage."""
    debits = [debit for debit in open_debits(customer).filter(kind=LedgerEntry.INVOICE) if debit.invoice.due_date]
    recorded = _recorded([debit.invoice_id for debit in debits])
    due = [(debit, stage) for debit in debits
           if (stage := due_stage(debit.invoice.due_date, today, recorded.get(debit.invoice_id, set())))]
    if not due:
        return None
    try:
        with transaction.atomic():
            email = CustomerEmail.objects.create(customer=customer, kind=CustomerEmail.REMINDER)
            kept = 0
            for debit, stage in due:
                try:
                    with transaction.atomic():
                        ReminderItem.objects.create(email=email, invoice_id=debit.invoice_id, stage=stage,
                                                    open_amount=debit.open)
                    kept += 1
                except IntegrityError:  # recorded by another run since we read: that one sends it
                    pass
            if not kept:
                raise _NothingToSend
    except _NothingToSend:
        return None
    queue_after_commit(email)
    return email


@shared_task
def run_reminders(today=None):
    """Daily (beat): reminders for every customer not closed that takes them by email."""
    today = today or uk_date()
    customers = (TradeCustomer.objects.exclude(status=TradeCustomer.CLOSED).filter(reminders_by_email=True)
                 .order_by('company_id', 'account_code'))
    sent = 0
    for customer in customers:
        try:  # one customer's problem never stops the others
            with transaction.atomic():
                sent += remind(customer, today) is not None
        except Exception:  # noqa: BLE001
            logger.exception('Reminders failed for customer %s', customer.pk)
    return sent


def reminder_message(email):
    company = email.customer.company
    items = list(email.reminder_items.select_related('invoice').order_by('invoice__due_date', 'invoice__number'))
    lines = [f'{item.invoice.number_display}  due {item.invoice.due_date:%d %B %Y}  ({LABELS[item.stage]})  '
             f'{item.open_amount} GBP' for item in items]
    total = sum(item.open_amount for item in items)
    body = ('This is a reminder about the following invoices:\n\n' + '\n'.join(lines)
            + f'\n\nTotal to pay: {total} GBP\n\nIf you have paid already, thank you, and please ignore this '
              f'email.\n\n{company.name}')
    return f'Payment reminder: invoices due to {company.name}', body, None
