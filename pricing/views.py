# pricing/views.py

from decimal import Decimal

from django.db import transaction
from django.http import FileResponse, Http404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from common.services.cost_calculation_service import CostCalculationService
from common.services.price_recommendation_service import PriceRecommendationService
from common.services.settings_service import get_company_setting
from common.tenancy import HasCompany, IsCompanyStaffOrReadOnly, SharedReferenceMixin, TenantScopedMixin, scope_queryset
from inventory.models import Product

from .models import InvoiceLineItem, PriceChangeLog, PricingPlan, Subscription, SupplierInvoice
from .serializers import (
    InvoiceLineItemSerializer,
    PriceChangeLogSerializer,
    PriceRecommendationRequestSerializer,
    PriceRecommendationSerializer,
    PricingPlanSerializer,
    SubscriptionSerializer,
    SupplierInvoiceSerializer,
)

DEFAULT_MARGIN_PERCENT = Decimal('30')


class PricingPlanViewSet(SharedReferenceMixin, viewsets.ModelViewSet):
    queryset = PricingPlan.objects.all()
    serializer_class = PricingPlanSerializer


class SubscriptionViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """Billing: anyone in the company can see subscriptions, only staff can change them."""

    queryset = Subscription.objects.all()
    serializer_class = SubscriptionSerializer
    permission_classes = [IsCompanyStaffOrReadOnly]


class SupplierInvoiceViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = SupplierInvoice.objects.all()
    serializer_class = SupplierInvoiceSerializer

    def perform_destroy(self, instance):
        stored = instance.file
        super().perform_destroy(instance)
        if stored:
            stored.delete(save=False)  # also remove the uploaded file from storage

    @extend_schema(responses={200: bytes})
    @action(detail=True, methods=['get'])
    def file(self, request, pk=None):
        """Download the uploaded invoice. Only people in the invoice's company can; the URL alone is not enough."""
        invoice = self.get_object()
        if not invoice.file:
            raise Http404('This invoice has no file.')
        return FileResponse(invoice.file.open('rb'), as_attachment=True, filename=f'invoice-{invoice.invoice_number}.{invoice.file.name.rsplit(".", 1)[-1]}')


class InvoiceLineItemViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = InvoiceLineItem.objects.all()
    serializer_class = InvoiceLineItemSerializer


class PriceChangeLogViewSet(
    TenantScopedMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Price changes awaiting or past approval. Staff request a change (or the nightly markdown job proposes one),
    approve it to apply the new price to the product, and the Shopify sync then pushes it to the store.
    """

    queryset = PriceChangeLog.objects.all()
    serializer_class = PriceChangeLogSerializer
    permission_classes = [IsCompanyStaffOrReadOnly]

    def perform_create(self, serializer):
        serializer.save(old_price=serializer.validated_data['product'].price)

    def destroy(self, request, *args, **kwargs):
        if self.get_object().is_approved:
            return Response({'detail': 'Approved price changes are a record and cannot be deleted.'}, status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)

    @extend_schema(request=None, responses=PriceChangeLogSerializer)
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        Approve the change: the product takes the new price now, and the next sync pushes it to Shopify.

        Refused (409) if the product's price is no longer the one the change was based on.
        """
        with transaction.atomic():
            log = self.get_object()
            if log.is_approved:
                return Response(self.get_serializer(log).data)  # approving twice is harmless
            product = Product.objects.select_for_update().get(pk=log.product_id)
            if product.price != log.old_price:
                return Response(
                    {'detail': f'The price is now {product.price}, not {log.old_price}. Delete this change and request a new one.'},
                    status=status.HTTP_409_CONFLICT,
                )
            product.price = log.new_price
            product.save(update_fields=['price'])
            log.is_approved = True
            log.save(update_fields=['is_approved'])
        return Response(self.get_serializer(log).data)


class PriceRecommendationView(APIView):
    """What a product costs to land, and what to sell it for: its price plus import duty, then a markup."""

    permission_classes = [HasCompany]

    @extend_schema(request=PriceRecommendationRequestSerializer, responses=PriceRecommendationSerializer)
    def post(self, request):
        serializer = PriceRecommendationRequestSerializer(data=request.data)
        serializer.fields['product'].queryset = scope_queryset(Product.objects.all(), request.user)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        product, quantity = data['product'], data['quantity']

        cost = CostCalculationService().landed_cost_breakdown(product, quantity, origin=data.get('origin') and data['origin'].upper())
        margin = data.get('margin_percent') or get_company_setting(
            product.company_id, 'default_margin_percent', DEFAULT_MARGIN_PERCENT, Decimal)
        unit_cost = (cost.total / quantity).quantize(Decimal('0.01'))
        try:
            recommended = PriceRecommendationService().calculate_recommended_price(unit_cost, margin)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(PriceRecommendationSerializer({
            'product': product.pk,
            'quantity': quantity,
            'goods_cost': cost.goods_cost,
            'duty_rate_percent': cost.duty_rate * 100,
            'duty_amount': cost.duty_amount,
            'duty_source': cost.duty_source,
            'note': cost.note,
            'landed_cost_total': cost.total,
            'landed_cost_per_unit': unit_cost,
            'margin_percent': margin,
            'recommended_unit_price': recommended,
            'current_unit_price': product.price,
        }).data)
