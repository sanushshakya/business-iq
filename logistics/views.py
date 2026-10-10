# logistics/views.py

from rest_framework import viewsets

from common.tenancy import TenantScopedMixin
from rest_framework.decorators import action
from rest_framework.response import Response

from common.models import FreightRateCache

from .models import AlternativeSupplier, Delivery, FreightAlert, LogisticProvider, UserSupplier
from .serializers import (
    AlternativeSupplierSerializer,
    DeliverySerializer,
    FreightAlertSerializer,
    FreightRateSerializer,
    LogisticProviderSerializer,
    UserSupplierSerializer,
)


class FreightAlertViewSet(TenantScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Alerts are raised by the freight-rate check; clients can read and dismiss them, not write them."""

    queryset = FreightAlert.objects.all()
    serializer_class = FreightAlertSerializer

    @action(detail=True, methods=['post'])
    def dismiss(self, request, pk=None):
        alert = self.get_object()
        alert.is_dismissed = True
        alert.save(update_fields=['is_dismissed'])
        return Response(self.get_serializer(alert).data)


class LogisticProviderViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = LogisticProvider.objects.all()
    serializer_class = LogisticProviderSerializer


class DeliveryViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Delivery.objects.all()
    serializer_class = DeliverySerializer


class UserSupplierViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """The company's own suppliers, with country of origin, categories and lead time."""

    queryset = UserSupplier.objects.all()
    serializer_class = UserSupplierSerializer


class AlternativeSupplierViewSet(TenantScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Suppliers that could replace the company's own. Read-only here; they are managed in the admin."""

    queryset = AlternativeSupplier.objects.all()
    serializer_class = AlternativeSupplierSerializer


class FreightRateViewSet(TenantScopedMixin, viewsets.ReadOnlyModelViewSet):
    """The latest freight rate seen for each of the company's shipping services."""

    queryset = FreightRateCache.objects.all()
    serializer_class = FreightRateSerializer
