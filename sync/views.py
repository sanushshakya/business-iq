# sync/views.py

from rest_framework import mixins, viewsets

from common.tenancy import IsCompanyStaffOrReadOnly, TenantScopedMixin

from .models import ShopifyConnection, SyncTask
from .serializers import ShopifyConnectionSerializer, SyncTaskSerializer


class SyncTaskViewSet(
    TenantScopedMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    """Sync runs. The id, status and timings are set by the system, so a task cannot be edited."""

    queryset = SyncTask.objects.all()
    serializer_class = SyncTaskSerializer


class ShopifyConnectionViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """The company's Shopify store. Anyone in the company can see it; only staff can connect, change or remove it."""

    permission_classes = [IsCompanyStaffOrReadOnly]
    queryset = ShopifyConnection.objects.all()
    serializer_class = ShopifyConnectionSerializer
