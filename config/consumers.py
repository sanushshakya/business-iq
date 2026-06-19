# config/consumers.py

import json
from channels.generic.websocket import WebsocketConsumer

class SyncConsumer(WebsocketConsumer):
    """
    WebSocket consumer for handling /ws/sync/ endpoint.

    Handles incoming WebSocket connections and messages.
    """

    def connect(self):
        """
        Establish a connection with the WebSocket client.
        """
        self.accept()

    def disconnect(self, close_code):
        """
        Handle disconnection of the WebSocket client.

        Args:
        - close_code (int): The code indicating the reason for closure.
        """
        pass

    def receive(self, text_data=None, bytes_data=None):
        """
        Receive a message from the WebSocket client.

        Args:
        - text_data (str, optional): Text data received from the client.
        - bytes_data (bytes, optional): Binary data received from the client.
        """
        if text_data is not None:
            try:
                message = json.loads(text_data)
                self.handle_message(message)
            except json.JSONDecodeError as e:
                self.send_error(f"Invalid JSON: {e}")

    def handle_message(self, message):
        """
        Process a received message.

        Args:
        - message (dict): The parsed message.
        """
        if "type" in message:
            message_type = message["type"]
            if message_type == "sync":
                self.sync(message)
            else:
                self.send_error(f"Unknown message type: {message_type}")
        else:
            self.send_error("Missing 'type' field in message")

    def sync(self, message):
        """
        Handle a synchronization request.

        Args:
        - message (dict): The parsed message.
        """
        # Placeholder for actual synchronization logic
        data = {
            "status": "success",
            "message": "Synchronization successful"
        }
        self.send(text_data=json.dumps(data))

    def send_error(self, error_message):
        """
        Send an error message to the client.

        Args:
        - error_message (str): The error message.
        """
        data = {
            "status": "error",
            "message": error_message
        }
        self.send(text_data=json.dumps(data))