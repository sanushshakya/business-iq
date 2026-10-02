# sync/views.py

from rest_framework import viewsets

from .models import ShopifyConnection, SyncTask
from .serializers import ShopifyConnectionSerializer, SyncTaskSerializer


class SyncTaskViewSet(viewsets.ModelViewSet):
    queryset = SyncTask.objects.all()
    serializer_class = SyncTaskSerializer


class ShopifyConnectionViewSet(viewsets.ModelViewSet):
    queryset = ShopifyConnection.objects.all()
    serializer_class = ShopifyConnectionSerializer
