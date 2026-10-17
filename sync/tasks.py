# sync/tasks.py

import json
import logging
from datetime import timedelta
from urllib.parse import urljoin

import requests
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import OutboundEvent, SyncTask
from .signing import sign

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 20
EVENTS_PATH = 'rewards/api/integration/events/'


@shared_task
def process_sync_task(task_id):
    """
    Mark a pending SyncTask as running and then completed (or failed). A task that already ran is left alone.

    The actual transfer between systems is not implemented yet; this only maintains task state.

    Returns True if the task finished successfully, False otherwise.
    """
    try:
        task = SyncTask.objects.get(task_id=task_id)
    except SyncTask.DoesNotExist:
        return False

    if not SyncTask.objects.filter(pk=task.pk, status='pending').update(status='running'):
        return False  # already picked up by another worker, or finished

    task.status = 'running'
    task.start_time = timezone.now()
    task.save(update_fields=['status', 'start_time'])
    try:
        # Placeholder for the real synchronisation work.
        task.status = 'completed'
        return True
    except Exception:
        task.status = 'failed'
        return False
    finally:
        task.end_time = timezone.now()
        task.save(update_fields=['status', 'end_time'])


def _record_failure(event, error):
    event.attempts += 1
    event.last_error = str(error)[:500]
    if event.attempts >= MAX_DELIVERY_ATTEMPTS:
        event.status = 'failed'
    else:
        event.next_attempt_at = timezone.now() + timedelta(seconds=min(2 ** event.attempts, 3600))
    event.save(update_fields=['attempts', 'last_error', 'status', 'next_attempt_at'])


@shared_task
def deliver_outbound_event(event_pk):
    """
    Send one due ``OutboundEvent`` to the storefront, signed with the connection's secret.

    Returns True only when this call delivered it. Any non-2xx or network error counts as an attempt and backs
    off ``min(2**attempts, 3600)`` seconds; after 20 attempts the event is ``failed``. m18 answers 200 for every
    signed event it understands (even a business "no"), so a retry only happens when it genuinely did not get it.
    Never logs the secret or the signature.
    """
    with transaction.atomic():
        event = (
            OutboundEvent.objects.select_for_update(skip_locked=True, of=('self',))
            .select_related('connection')
            .filter(pk=event_pk, status='pending', next_attempt_at__lte=timezone.now(),
                    connection__is_active=True)
            .exclude(connection__base_url='')
            .first()
        )
        if event is None:
            return False

        connection = event.connection
        body = json.dumps(event.payload, separators=(',', ':'), sort_keys=True).encode()  # these bytes are signed
        try:
            response = requests.post(
                urljoin(connection.base_url, EVENTS_PATH),
                data=body,
                headers={'Content-Type': 'application/json', 'X-Integration-Signature': sign(connection.webhook_secret, body)},
                timeout=5,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.warning('Storefront delivery of event %s failed: %s', event.event_id, type(exc).__name__)
            _record_failure(event, type(exc).__name__)
            return False

        if 200 <= response.status_code < 300:
            event.status = 'delivered'
            event.last_error = ''
            event.save(update_fields=['status', 'last_error'])
            return True

        logger.warning('Storefront delivery of event %s failed: HTTP %s', event.event_id, response.status_code)
        _record_failure(event, f'HTTP {response.status_code}: {(response.text or "")[:200]}')
        return False


@shared_task
def deliver_outbound_events(batch_size=50):
    """Sweep (every 30 s): retry due pending events on active connections. Returns the number delivered."""
    due = list(
        OutboundEvent.objects.filter(status='pending', next_attempt_at__lte=timezone.now(), connection__is_active=True)
        .exclude(connection__base_url='')
        .order_by('next_attempt_at')
        .values_list('pk', flat=True)[:batch_size]
    )
    return sum(1 for pk in due if deliver_outbound_event(pk))
