"""
Connecting a company to HMRC (G56): OAuth 2.0 authorisation code grant. ``start`` keeps only hashes of the state and
of a browser nonce (an HttpOnly cookie the view sets) and returns HMRC's sign-in URL; ``finish`` runs on HMRC's
redirect, which carries no token of ours: the single-use 10-minute state, arriving in the browser that started it,
from a user still staff of that company, is what ties it to them. Someone else's browser can't finish it.
"""

import hashlib
import re
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounting.models import HmrcAuthState, HmrcConnection

from . import client


class ConnectError(ValueError):
    pass


def _hash(state):
    return hashlib.sha256(state.encode()).hexdigest()


def vrn_of(company):
    """The VRN HMRC knows the company by: the first 9 digits of its VAT number (a 12-digit number adds a branch)."""
    digits = re.sub(r'\D', '', company.vat_number or '')
    return digits[:9] if len(digits) >= 9 else ''


def start(company, user):
    """A new sign-in for ``company`` started by ``user``: ``(HMRC's authorize URL, browser nonce for the cookie)``."""
    if settings.HMRC_VAT_ENVIRONMENT == 'off':
        raise ConnectError('Filing with HMRC is not switched on (HMRC_VAT_ENVIRONMENT).')
    if not vrn_of(company):
        raise ConnectError("Add the company's VAT number first.")
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    HmrcAuthState.objects.create(company=company, user=user, state_hash=_hash(state), browser_hash=_hash(browser))
    query = urlencode({'response_type': 'code', 'client_id': settings.HMRC_CLIENT_ID, 'scope': client.SCOPE,
                       'state': state, 'redirect_uri': settings.HMRC_REDIRECT_URI})
    return f'{settings.HMRC_BASE_URL}/oauth/authorize?{query}', browser


def _take_state(state):
    """The state's row, deleted (single use), or None when unknown or expired. Expired ones are cleared too."""
    HmrcAuthState.objects.filter(created_at__lt=timezone.now() - HmrcAuthState.LIFETIME).delete()
    if not state:
        return None
    with transaction.atomic():
        row = HmrcAuthState.objects.select_for_update().filter(state_hash=_hash(state)).first()
        if row is not None:
            HmrcAuthState.objects.filter(pk=row.pk).delete()
    return row


def _still_allowed(row, browser):
    user = row.user
    return (bool(browser) and secrets.compare_digest(_hash(browser), row.browser_hash) and user.is_active
            and (user.is_staff or user.is_superuser) and user.company_id == row.company_id)


def finish(params, browser):
    """Handle HMRC's redirect in the browser that sent ``browser`` (its cookie). Returns ``'connected'`` or a
    reason: ``state`` (unknown, used, expired, another browser, or the user no longer staff there), ``denied`` or
    ``exchange``."""
    row = _take_state(params.get('state', ''))
    if row is None or not _still_allowed(row, browser):
        return 'state'
    if params.get('error') or not params.get('code'):
        return 'denied'
    try:
        tokens = client.exchange_code(params['code'])
    except client.HmrcError:
        return 'exchange'
    connection = HmrcConnection.objects.filter(company=row.company).first() or HmrcConnection(company=row.company)
    client.store_tokens(connection, tokens, new_grant=True)
    connection.vrn = vrn_of(row.company)
    connection.environment = settings.HMRC_VAT_ENVIRONMENT
    connection.connected_by = row.user
    connection.connected_at = timezone.now()
    connection.save()
    return 'connected'
