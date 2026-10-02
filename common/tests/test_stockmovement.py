"""
tests/test_stockmovement.py
==================================

This module contains integration tests for StockMovement events via WebSocket using pytest-django and Channels.
"""

import pytest
from channels.testing import WebsocketCommunicator
from django.test.runner import DiscoverRunner
from channels.layers import get_channel_layer
from channels.sessions import channel_session_user_from_headers
from django.contrib.auth.models import User

pytestmark = pytest.mark.django_db(transaction=True)

async def create_test_connection(user):
    """
    Create a test WebSocket connection for the given user.
    """
    communicator = WebsocketCommunicator(get_channel_layer(), 'stockmovement.websocket')
    await communicator.connect()
    return communicator

@pytest.fixture
def stock_movement_event():
    """
    Fixture to simulate a StockMovement event.
    """
    # Simulate an event here, for example:
    return {"type": "stock_moved", "data": {"product_id": 123}}

async def test_stockmovement_websocket(stock_movement_event):
    """
    Integration test for handling StockMovement events via WebSocket.
    """
    user = User.objects.create_user(username='testuser', password='testpassword')
    communicator = await create_test_connection(user)

    # Simulate receiving a stock movement event
    await communicator.send_json_to({"type": "stock_moved", "data": {"product_id": 123}})

    response = await communicator.receive_json_from()

    assert response == {
        'type': 'websocket.response',
        'message': 'Stock movement received successfully'
    }

    # Ensure the message is not sent twice
    with pytest.raises(TimeoutError):
        await communicator.receive_json_from(timeout=1)

    await communicator.disconnect()