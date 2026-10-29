"""``/ws/sync/``: a company's live storefront sync status (``sync.status``) for the web app's header badge."""

import time

import jwt
from asgiref.sync import async_to_sync
from channels.generic.websocket import JsonWebsocketConsumer
from django.contrib.auth import get_user_model

from authentication.jwt_handler import decode_token, password_stamp

from .status import group_name, status_for

PROTOCOL = 'bearer'
SIGN_IN_AGAIN = 4401  # close code: the token was refused or has run out; the web app refreshes it and reconnects


def _payload(subprotocols):
    """The decoded access token sent as the subprotocols ``["bearer", <token>]``, or None."""
    if len(subprotocols) != 2 or subprotocols[0] != PROTOCOL:
        return None
    try:
        return decode_token(subprotocols[1])
    except jwt.InvalidTokenError:
        return None


def _allowed(user_id, pw, company_id=None):
    """
    The user if they may see a company's sync status: active, same password as the token, not an operator
    (default deny, as in ``role_access``), with a company, and (when given) still in ``company_id``. Else None.
    """
    user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
    if user is None or pw != password_stamp(user) or user.is_operator or not user.company_id:
        return None
    if company_id is not None and user.company_id != company_id:
        return None
    return user


class SyncStatusConsumer(JsonWebsocketConsumer):
    """
    Sends the status on connect and whenever ``sync.status.broadcast`` runs for the user's company. Read-only.

    The token is checked at connect (browsers cannot set headers on a websocket, and a query string ends up in
    logs, so it comes as the subprotocol) and again before every message: a socket whose token expired or whose
    user was deactivated, changed password or moved company is closed with 4401 and receives nothing more.
    """

    group = None
    claims = None

    def connect(self):
        subprotocols = self.scope.get('subprotocols') or []
        payload = _payload(subprotocols)
        user = payload and _allowed(payload.get('user_id'), payload.get('pw'))
        # Accept before refusing, so the browser sees 4401 rather than a bare failed handshake.
        self.accept(subprotocol=PROTOCOL if subprotocols[:1] == [PROTOCOL] else None)
        if not user:
            self.close(code=SIGN_IN_AGAIN)
            return
        self.claims = {'user_id': user.pk, 'pw': payload['pw'], 'exp': payload['exp'], 'company_id': user.company_id}
        self.group = group_name(user.company_id)
        async_to_sync(self.channel_layer.group_add)(self.group, self.channel_name)
        self.send_json(status_for(user.company_id))

    def disconnect(self, close_code):
        if self.group:
            async_to_sync(self.channel_layer.group_discard)(self.group, self.channel_name)

    def sync_status(self, message):
        claims = self.claims
        if claims is None:
            return
        if time.time() >= claims['exp'] or not _allowed(claims['user_id'], claims['pw'], claims['company_id']):
            self.claims = None
            async_to_sync(self.channel_layer.group_discard)(self.group, self.channel_name)
            self.group = None
            self.close(code=SIGN_IN_AGAIN)
            return
        self.send_json(message)
