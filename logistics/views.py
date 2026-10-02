# logistics/views.py

from rest_framework import viewsets

from common.tenancy import TenantScopedMixin
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Delivery, FreightAlert, LogisticProvider
from .serializers import DeliverySerializer, FreightAlertSerializer, LogisticProviderSerializer


class FreightAlertViewSet(TenantScopedMixin, viewsets.ModelViewSet):
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
