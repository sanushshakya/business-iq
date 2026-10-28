# sync/loyalty.py

"""
Loyalty card lookup at the till, against the m18 storefront (which owns loyalty accounts and customer data).

This is the one call where a person waits on m18, so it gives up after 1.5 s and the till sells without points.
Hits are cached for 60 s under a hash of the connection and card, so raw card numbers are never cache keys.
"""

import hashlib
import json
import logging
from urllib.parse import urljoin

import requests
from django.core.cache import cache

from .signing import sign

logger = logging.getLogger(__name__)

LOOKUP_PATH = 'rewards/api/integration/lookup/'
TIMEOUT = 1.5
CACHE_SECONDS = 60
FIELDS = ('first_name', 'points_balance', 'tier', 'points_gbp_value')


class LoyaltyUnavailable(Exception):
    """m18 did not answer in time, or answered with an error."""


def _cache_key(connection, barcode):
    return 'loyalty:' + hashlib.sha256(f'{connection.pk}:{barcode}'.encode()).hexdigest()


def lookup(connection, barcode):
    """The card holder's ``{first_name, points_balance, tier, points_gbp_value}``, or None for an unknown card."""
    key = _cache_key(connection, barcode)
    hit = cache.get(key)
    if hit is not None:
        return hit

    body = json.dumps({'barcode': barcode}).encode()
    try:
        response = requests.post(
            urljoin(connection.base_url, LOOKUP_PATH),
            data=body,
            headers={'Content-Type': 'application/json', 'X-Integration-Signature': sign(connection.webhook_secret, body)},
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        logger.warning('Loyalty lookup failed: %s', type(exc).__name__)
        raise LoyaltyUnavailable from exc

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        logger.warning('Loyalty lookup failed: HTTP %s', response.status_code)
        raise LoyaltyUnavailable
    try:
        data = response.json()
        customer = {field: data[field] for field in FIELDS}  # only what the till shows, whatever m18 sends
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning('Loyalty lookup returned an unexpected body')
        raise LoyaltyUnavailable from exc
    cache.set(key, customer, CACHE_SECONDS)
    return customer
