"""
HTTP to HMRC's MTD API (G56): OAuth tokens and calls. Tokens, the client secret and the authorisation code never
reach a log, an exception or a response; failures are logged as the HTTP status and HMRC's error code only.
Tests patch ``SESSION``.
"""

import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounting.models import HmrcConnection

logger = logging.getLogger('accounting.hmrc')

SESSION = requests.Session()
ACCEPT = 'application/vnd.hmrc.1.0+json'
SCOPE = 'read:vat write:vat'
REFRESH_LIFETIME = timedelta(days=548)  # HMRC refresh tokens last 18 months
REFRESH_MARGIN = timedelta(seconds=60)


class HmrcError(Exception):
    """HMRC said no (``status`` its HTTP status) or did not answer (``status`` None)."""

    def __init__(self, status, code, message):
        super().__init__(f'{status} {code}')
        self.status, self.code, self.message = status, code, message


def _no_answer():
    return HmrcError(None, 'NO_ANSWER', 'HMRC did not answer.')


def _token_request(data):
    body = dict(data, client_id=settings.HMRC_CLIENT_ID, client_secret=settings.HMRC_CLIENT_SECRET)
    try:
        response = SESSION.post(f'{settings.HMRC_BASE_URL}/oauth/token', data=body,
                                timeout=settings.HMRC_TIMEOUT_SECONDS)
    except requests.RequestException:
        logger.warning('HMRC token request: no answer')
        raise _no_answer() from None
    if response.status_code >= 400:
        code = json_of(response).get('error') or 'TOKEN_REFUSED'
        logger.warning('HMRC token request refused: %s %s', response.status_code, code)
        raise HmrcError(response.status_code, str(code)[:60], 'HMRC refused the sign-in.')
    return json_of(response)


def json_of(response):
    try:
        value = response.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def exchange_code(code):
    """Swap the authorisation code from HMRC's redirect for tokens (the token answer as a dict)."""
    return _token_request({'grant_type': 'authorization_code', 'code': code,
                           'redirect_uri': settings.HMRC_REDIRECT_URI})


def store_tokens(connection, tokens, new_grant=False):
    """Copy a token answer onto ``connection`` (not saved). The 18 months run from the grant, not each refresh."""
    now = timezone.now()
    connection.access_token = tokens['access_token']
    connection.refresh_token = tokens['refresh_token']
    connection.access_expires_at = now + timedelta(seconds=int(tokens.get('expires_in', 14400)))
    if new_grant or connection.refresh_expires_at is None:
        connection.refresh_expires_at = now + REFRESH_LIFETIME
    connection.scope = tokens.get('scope', SCOPE)[:100]


def _reconnect():
    return HmrcError(401, 'RECONNECT', 'Connect to HMRC again.')


def access_token(connection):
    """A usable access token for ``connection``, refreshed first when under a minute is left. Refresh tokens are
    single use, so the row is locked while refreshing; a refused refresh deletes the connection."""
    if connection.environment != settings.HMRC_VAT_ENVIRONMENT:
        raise _reconnect()
    refused = False
    with transaction.atomic():
        current = HmrcConnection.objects.select_for_update().get(pk=connection.pk)
        if current.access_expires_at - timezone.now() > REFRESH_MARGIN:
            return current.access_token
        try:
            tokens = _token_request({'grant_type': 'refresh_token', 'refresh_token': current.refresh_token})
        except HmrcError as error:
            if error.status not in (400, 401):
                raise
            refused = True
        else:
            store_tokens(current, tokens)
            current.save()
            return current.access_token
    if refused:  # outside the atomic block, so the delete is kept
        HmrcConnection.objects.filter(pk=connection.pk).delete()
        raise _reconnect()


def _error(response):
    body = json_of(response)
    errors = body.get('errors') or []
    first = errors[0] if errors and isinstance(errors[0], dict) else body
    code = str(first.get('code') or body.get('code') or f'HTTP_{response.status_code}')[:60]
    message = str(first.get('message') or body.get('message') or 'HMRC refused the request.')[:500]
    return HmrcError(response.status_code, code, message)


def call(connection, method, path, *, fraud, json=None, params=None, test_scenario=None):
    """Call HMRC for ``connection`` with the fraud-prevention headers; raises ``HmrcError`` on a refusal or no
    answer. ``test_scenario`` (``Gov-Test-Scenario``) is only ever sent to the sandbox."""
    token = access_token(connection)
    headers = dict(fraud, Authorization=f'Bearer {token}', Accept=ACCEPT)
    if test_scenario and settings.HMRC_VAT_ENVIRONMENT == 'sandbox':
        headers['Gov-Test-Scenario'] = test_scenario
    try:
        response = SESSION.request(method, f'{settings.HMRC_BASE_URL}{path}', headers=headers, json=json,
                                   params=params, timeout=settings.HMRC_TIMEOUT_SECONDS)
    except requests.RequestException:
        logger.warning('HMRC %s: no answer', method)
        raise _no_answer() from None
    if response.status_code >= 400:
        error = _error(response)
        logger.warning('HMRC %s refused: %s %s', method, error.status, error.code)
        raise error
    return response
