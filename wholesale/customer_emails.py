"""
Sending the G51 emails logged as ``CustomerEmail`` rows: a statement (with its PDF) or a reminder (plain text).
The row is made in the transaction that decides to send, so a second run never makes another; this task only
sends it and records the outcome. It never raises, and never logs an address or an SMTP message (they quote it).
A row the task never took (the broker was down, a worker died first) is sent by ``sweep_customer_emails``; one whose
send was interrupted may have gone, so it is marked failed, never sent again.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from .invoices import recipient
from .models import CustomerEmail

logger = logging.getLogger('wholesale.statements')


def queue_after_commit(email):
    pk = email.pk
    transaction.on_commit(lambda: _queue(pk))


def _queue(email_id):
    try:
        deliver_customer_email.apply_async((email_id,), retry=False)
    except Exception:  # noqa: BLE001 - it stays queued; the sweep sends it once the broker is back
        logger.warning('Could not queue customer email %s', email_id, exc_info=True)


@shared_task
def deliver_customer_email(email_id):
    from .reminders import reminder_message
    from .statements import statement_message

    # Claimed first: a redelivered task, or a second worker, finds it taken and sends nothing.
    if not CustomerEmail.objects.filter(pk=email_id, status=CustomerEmail.QUEUED).update(
            status=CustomerEmail.SENDING):
        return
    email = CustomerEmail.objects.select_related('customer__company').get(pk=email_id)
    to = recipient(email.customer)
    if not to:
        _outcome(email, CustomerEmail.NO_ADDRESS, '')
        return
    try:  # building the PDF or the text can fail too: the log says failed, not queued for ever
        subject, body, attachment = (statement_message if email.kind == CustomerEmail.STATEMENT
                                     else reminder_message)(email)
        message = EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[to])
        if attachment:
            message.attach(*attachment)
        message.send(fail_silently=False)
    except Exception as error:  # noqa: BLE001 - SMTP errors are many and varied
        logger.warning('Could not email %s %s (%s)', email.kind, email.pk, type(error).__name__)
        _outcome(email, CustomerEmail.FAILED, to, type(error).__name__)
        return
    _outcome(email, CustomerEmail.SENT, to)


def _outcome(email, status, to, error=''):
    email.status, email.to, email.error = status, to, error[:60]
    email.sent_at = timezone.now() if status == CustomerEmail.SENT else None
    email.save(update_fields=['status', 'to', 'error', 'sent_at'])


SWEEP_AFTER = timedelta(minutes=10)
GIVE_UP_AFTER = timedelta(days=3)
SENDING_FOR = timedelta(hours=1)


@shared_task
def sweep_customer_emails():
    """Every 15 minutes (beat): queue again what is still queued after 10 minutes; give up after 3 days. A send
    interrupted for an hour is marked failed (it may have gone)."""
    now = timezone.now()
    CustomerEmail.objects.filter(status=CustomerEmail.QUEUED, created_at__lt=now - GIVE_UP_AFTER).update(
        status=CustomerEmail.FAILED, error='NotSent')
    CustomerEmail.objects.filter(status=CustomerEmail.SENDING, created_at__lt=now - SENDING_FOR).update(
        status=CustomerEmail.FAILED, error='Interrupted')
    waiting = CustomerEmail.objects.filter(status=CustomerEmail.QUEUED, created_at__lt=now - SWEEP_AFTER)
    for email_id in waiting.values_list('pk', flat=True)[:500]:
        _queue(email_id)
