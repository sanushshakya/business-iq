# sync/tasks.py

import json
import logging
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urljoin

import requests
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import OutboundEvent, StockReconciliation, StockSyncState, StorefrontConnection, SyncTask
from .signing import sign

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 20
EVENTS_PATH = 'integration/events/'
STOCK_BATCH_SIZE = 500
STOCK_SNAPSHOT_PATH = 'integration/stock/'


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


def _price(value):
    return str(Decimal(value).quantize(Decimal('0.01')))


def _push_stock_batch(connection):
    """Send up to ``STOCK_BATCH_SIZE`` pending products for ``connection``. Returns ``(sent, maybe_more)``."""
    from .outbox import emit
    from .stock import online_available

    with transaction.atomic():
        states = list(
            StockSyncState.objects.select_for_update(skip_locked=True, of=('self',))
            .select_related('product')
            .filter(pending=True, product__company_id=connection.company_id)
            .order_by('pk')[:STOCK_BATCH_SIZE]
        )
        if not states:
            return 0, False
        linked = [state for state in states if state.product.external_id]
        available = online_available([state.product for state in linked])
        items = []
        for state in linked:
            state.version += 1
            state.last_sent_available = available[state.product_id]
            item = {
                'product_id': state.product.external_id,
                'online_available': state.last_sent_available,
                'version': state.version,
            }
            if connection.push_prices:
                item['price'] = _price(state.product.price)
            items.append(item)
        for state in states:
            state.pending = False
        StockSyncState.objects.bulk_update(states, ['pending', 'version', 'last_sent_available'])
        if items:
            emit(connection, 'stock.changed', {'items': items})
        return len(items), len(states) == STOCK_BATCH_SIZE


@shared_task
def push_storefront_stock():
    """
    Sweep (every 15 s): send the online stock of every pending linked product, as absolute, versioned
    snapshots in ``stock.changed`` events (one per ``STOCK_BATCH_SIZE`` products). Returns how many were sent.
    """
    sent = 0
    for connection in StorefrontConnection.objects.filter(is_active=True).exclude(base_url=''):
        more = True
        while more:
            count, more = _push_stock_batch(connection)
            sent += count
    return sent


def _storefront_stock(connection):
    """m18's ``{product_id: item}`` for every product it has. Raises ``RuntimeError`` with a short reason."""
    body = b'{}'
    try:
        response = requests.post(
            urljoin(connection.base_url, STOCK_SNAPSHOT_PATH),
            data=body,
            headers={'Content-Type': 'application/json', 'X-Integration-Signature': sign(connection.webhook_secret, body)},
            timeout=30,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise RuntimeError(type(exc).__name__) from exc
    if response.status_code != 200:
        raise RuntimeError(f'HTTP {response.status_code}')
    try:
        return {item['product_id']: item for item in response.json()['items']}
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError('Unexpected response') from exc


def _compare(connection, theirs):
    from .stock import linked_products, online_available

    products = list(linked_products().filter(company_id=connection.company_id))
    ours = online_available(products)
    mismatches = []
    for product in products:
        item = theirs.get(product.external_id)
        if item is None:
            mismatches.append({'product_id': product.external_id, 'kind': 'missing_in_m18',
                               'iq': ours[product.pk], 'm18': None})
            continue
        if item.get('iq_available') != ours[product.pk]:
            mismatches.append({'product_id': product.external_id, 'kind': 'different',
                               'iq': ours[product.pk], 'm18': item.get('iq_available')})
        if 'price' in item and item['price'] != _price(product.price):
            mismatches.append({'product_id': product.external_id, 'kind': 'price_different',
                               'iq': _price(product.price), 'm18': item['price']})
    linked_ids = {product.external_id for product in products}
    for product_id, item in theirs.items():
        if item.get('iq_managed') and product_id not in linked_ids:
            mismatches.append({'product_id': product_id, 'kind': 'not_linked_in_iq',
                               'iq': None, 'm18': item.get('iq_available')})
    return len(products), mismatches


@shared_task
def reconcile_storefront_stock():
    """
    Nightly: compare IQ's online stock (and prices) with the storefront's, record the differences (``StockReconciliation``),
    then queue every linked product so the next sweep resends it. That fixes any drift and applies stock that
    expired overnight. A sale between the two reads can show as a difference; the resend corrects it anyway.
    """
    from .stock import mark_all

    for connection in StorefrontConnection.objects.filter(is_active=True).exclude(base_url=''):
        run = StockReconciliation(connection=connection)
        try:
            run.checked, run.mismatches = _compare(connection, _storefront_stock(connection))
        except RuntimeError as exc:
            run.error = str(exc)[:200]
            logger.warning('Storefront stock check for connection %s failed: %s', connection.pk, run.error)
        run.save()
        if run.mismatches:
            logger.warning('Storefront stock check for connection %s found %s mismatch(es); resending all',
                           connection.pk, len(run.mismatches))
        mark_all(connection.company_id)
