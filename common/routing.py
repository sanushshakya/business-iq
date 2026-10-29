# common/routing.py

from django.urls import path

from sync.consumers import SyncStatusConsumer

websocket_urlpatterns = [
    path('ws/sync/', SyncStatusConsumer.as_asgi()),
]
