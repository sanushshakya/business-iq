# pricing/views.py

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import InvoiceLineItem, PriceChangeLog, PricingPlan, Subscription, SupplierInvoice
from .serializers import (
    InvoiceLineItemSerializer,
    PriceChangeLogSerializer,
    PricingPlanSerializer,
    SubscriptionSerializer,
    SupplierInvoiceSerializer,
)


class PricingPlanViewSet(viewsets.ModelViewSet):
    queryset = PricingPlan.objects.all()
    serializer_class = PricingPlanSerializer


class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = Subscription.objects.all()
    serializer_class = SubscriptionSerializer


class SupplierInvoiceViewSet(viewsets.ModelViewSet):
    queryset = SupplierInvoice.objects.all()
    serializer_class = SupplierInvoiceSerializer


class InvoiceLineItemViewSet(viewsets.ModelViewSet):
    queryset = InvoiceLineItem.objects.all()
    serializer_class = InvoiceLineItemSerializer


class PriceChangeLogViewSet(viewsets.ModelViewSet):
    queryset = PriceChangeLog.objects.all()
    serializer_class = PriceChangeLogSerializer

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a price change so the next sync pushes it to Shopify."""
        log = self.get_object()
        log.is_approved = True
        log.save(update_fields=['is_approved'])
        return Response(self.get_serializer(log).data)
