"""
Sending the G51 emails logged as ``CustomerEmail`` rows: a statement (with its PDF) or a reminder (plain text).
The row is made in the transaction that decides to send, so a second run never makes another; this task only
sends it and records the outcome. It never raises, and never logs an address or an SMTP message (they quote it).
"""

import logging

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
    except Exception:  # noqa: BLE001 - a broker problem leaves it queued in the log
        logger.warning('Could not queue customer email %s', email_id, exc_info=True)


@shared_task
def deliver_customer_email(email_id):
    from .reminders import reminder_message
    from .statements import statement_message

    email = CustomerEmail.objects.select_related('customer__company').filter(
        pk=email_id, status=CustomerEmail.QUEUED).first()
    if email is None:
        return
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
