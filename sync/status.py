"""
The storefront sync status a company's staff see in the web app's header (``/ws/sync/``, ``sync.consumers``).

``status_for`` summarises the company's outbox; ``broadcast`` sends it to the company's own channel group, so
one company never receives another's. The tasks that change it (a delivery attempt, a stock push) call
``broadcast``; it never raises into them.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import OutboundEvent, StorefrontConnection

logger = logging.getLogger(__name__)


def group_name(company_id):
    return f'sync-status-{company_id}'


def status_for(company_id):
    """
    ``{type, state, last_push_at, backlog, failed, last_error}`` for the company's storefront connection.

    ``state``: ``off`` without an active connection; ``red`` when an event failed or is being retried after an
    error; ``amber`` while events wait to be sent; otherwise ``green``.
    """
    connection = (StorefrontConnection.objects.filter(company_id=company_id, is_active=True)
                  .exclude(base_url='').first())
    status = {'type': 'sync.status', 'state': 'off', 'last_push_at': None, 'backlog': 0, 'failed': 0, 'last_error': ''}
    if connection is None:
        return status
    events = OutboundEvent.objects.filter(connection=connection)
    last_push = events.filter(event_type='stock.changed', status='delivered').order_by('-pk').first()
    erroring = events.filter(status__in=['pending', 'failed']).exclude(last_error='').order_by('-pk').first()
    status.update(
        last_push_at=last_push.created_at.isoformat() if last_push else None,
        backlog=events.filter(status='pending').count(),
        failed=events.filter(status='failed').count(),
        last_error=erroring.last_error[:200] if erroring else '',
    )
    status['state'] = 'red' if status['failed'] or erroring else 'amber' if status['backlog'] else 'green'
    return status


def broadcast(company_id):
    """Send the company's current status to its open web app sessions. Logs, never raises."""
    try:
        async_to_sync(get_channel_layer().group_send)(group_name(company_id), status_for(company_id))
    except Exception as exc:  # the channel layer (Redis) is optional to the work that calls this
        logger.warning('Sync status broadcast for company %s failed: %s', company_id, type(exc).__name__)
