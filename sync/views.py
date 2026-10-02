# sync/views.py

from rest_framework import viewsets

from common.tenancy import TenantScopedMixin

from .models import ShopifyConnection, SyncTask
from .serializers import ShopifyConnectionSerializer, SyncTaskSerializer


class SyncTaskViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = SyncTask.objects.all()
    serializer_class = SyncTaskSerializer


class ShopifyConnectionViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = ShopifyConnection.objects.all()
    serializer_class = ShopifyConnectionSerializer
