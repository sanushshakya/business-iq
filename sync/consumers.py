"""``/ws/sync/``: a company's live storefront sync status (``sync.status``) for the web app's header badge."""

import jwt
from asgiref.sync import async_to_sync
from channels.generic.websocket import JsonWebsocketConsumer
from django.contrib.auth import get_user_model

from authentication.jwt_handler import decode_token, password_stamp

from .status import group_name, status_for

PROTOCOL = 'bearer'


def user_for(subprotocols):
    """
    The user for the access token sent as the subprotocols ``["bearer", <token>]`` (browsers cannot set headers
    on a websocket, and a query string ends up in logs), or None. Same checks as ``JWTAuthentication``; operators
    are refused (default deny, as in ``role_access``) and so are users without a company.
    """
    if len(subprotocols) != 2 or subprotocols[0] != PROTOCOL:
        return None
    try:
        payload = decode_token(subprotocols[1])
    except jwt.InvalidTokenError:
        return None
    user = get_user_model().objects.filter(pk=payload.get('user_id'), is_active=True).first()
    if user is None or payload.get('pw') != password_stamp(user) or user.is_operator or not user.company_id:
        return None
    return user


class SyncStatusConsumer(JsonWebsocketConsumer):
    """Sends the status on connect and whenever ``sync.status.broadcast`` runs for the user's company. Read-only."""

    group = None

    def connect(self):
        user = user_for(self.scope.get('subprotocols') or [])
        if user is None:
            self.close()
            return
        self.group = group_name(user.company_id)
        async_to_sync(self.channel_layer.group_add)(self.group, self.channel_name)
        self.accept(subprotocol=PROTOCOL)
        self.send_json(status_for(user.company_id))

    def disconnect(self, close_code):
        if self.group:
            async_to_sync(self.channel_layer.group_discard)(self.group, self.channel_name)

    def sync_status(self, message):
        self.send_json(message)
