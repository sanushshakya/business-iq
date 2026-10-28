# sync/outbox.py

"""
Events IQ sends to the m18 storefront (``loyalty.award`` / ``loyalty.reverse``), via a transactional outbox.

``emit`` writes an ``OutboundEvent`` inside the caller's transaction, so a rollback drops it, and hands it to a
worker the moment the transaction commits. The envelope matches the one m18 sends to IQ.
"""

import logging
import uuid

from django.db import transaction
from django.utils import timezone

from .models import OutboundEvent, StorefrontConnection
from .tasks import deliver_outbound_event

logger = logging.getLogger(__name__)


def active_connection(company_id):
    """The company's storefront connection if it is switched on and has somewhere to send to, else None."""
    return (
        StorefrontConnection.objects.filter(company_id=company_id, is_active=True)
        .exclude(base_url='')
        .first()
    )


def _dispatch(pk):
    # The sale has already committed: a broker problem must not turn it into an error. The sweep sends it later.
    try:
        deliver_outbound_event.apply_async((pk,), retry=False)
    except Exception:
        logger.warning('Could not queue storefront event %s; the sweep will send it', pk, exc_info=True)


def emit(connection, event_type, data):
    event_id = uuid.uuid4()
    event = OutboundEvent.objects.create(
        connection=connection,
        event_id=event_id,
        event_type=event_type,
        payload={
            'event_id': str(event_id),
            'event_type': event_type,
            'occurred_at': timezone.now().isoformat().replace('+00:00', 'Z'),
            'data': data,
        },
    )
    transaction.on_commit(lambda: _dispatch(event.pk))
    return event
