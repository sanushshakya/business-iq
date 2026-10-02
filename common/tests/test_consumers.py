# common/tests/test_consumers.py

import json

from channels.testing import WebsocketCommunicator
from django.test import TestCase

from common.routing import websocket_urlpatterns
from common.consumers import SyncConsumer


class SyncConsumerTests(TestCase):
    async def connect(self):
        communicator = WebsocketCommunicator(SyncConsumer.as_asgi(), '/ws/sync/')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        return communicator

    def test_route_is_registered(self):
        self.assertEqual(str(websocket_urlpatterns[0].pattern), 'ws/sync/')

    async def test_sync_message(self):
        communicator = await self.connect()
        await communicator.send_to(text_data=json.dumps({'type': 'sync'}))
        response = json.loads(await communicator.receive_from())
        self.assertEqual(response['status'], 'success')
        await communicator.disconnect()

    async def test_unknown_type_and_invalid_json(self):
        communicator = await self.connect()
        await communicator.send_to(text_data=json.dumps({'type': 'nope'}))
        self.assertEqual(json.loads(await communicator.receive_from())['status'], 'error')
        await communicator.send_to(text_data='not json')
        self.assertEqual(json.loads(await communicator.receive_from())['status'], 'error')
        await communicator.disconnect()
