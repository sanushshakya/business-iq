from datetime import timedelta

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from common.tenancy import TenantScopedMixin
from inventory.views import CompanyMemberReadsStaffWrites
from vat.services import uk_date

from .drafting import draft_orders
from .models import PurchaseOrder, PurchaseOrderLine, SupplierProduct
from .serializers import (
    DraftSummarySerializer,
    LineEditSerializer,
    PurchaseOrderSerializer,
    SupplierProductSerializer,
)

STATUSES = {value for value, _label in PurchaseOrder.STATUSES}


class SupplierProductViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """Which supplier sells each product, in what packs and at what cost (G57). One preferred supplier per product:
    orders are drafted to it. Members read; staff change."""

    queryset = SupplierProduct.objects.select_related('supplier', 'product')
    serializer_class = SupplierProductSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def get_queryset(self):
        return super().get_queryset().filter(supplier__company_id=self.request.user.company_id)

    def _save(self, save, serializer):
        try:
            with transaction.atomic():
                save(serializer)
        except IntegrityError:
            raise ValidationError({'detail': 'This supplier has this product already, or the product has another '
                                             'preferred supplier.'}) from None

    def perform_create(self, serializer):
        self._save(super().perform_create, serializer)

    def perform_update(self, serializer):
        self._save(super().perform_update, serializer)


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('status', str, enum=sorted(STATUSES)), OpenApiParameter('supplier', int),
]))
class PurchaseOrderViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                           viewsets.GenericViewSet):
    """Purchase orders (G57), drafted nightly or on demand from stock projections; never sent from here. Staff edit a
    draft's packs (it is then kept by later runs), approve it or cancel it."""

    queryset = PurchaseOrder.objects.select_related('supplier').prefetch_related('lines__product')
    serializer_class = PurchaseOrderSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def get_queryset(self):
        queryset = super().get_queryset().filter(company_id=self.request.user.company_id)
        params = self.request.query_params
        if params.get('status'):
            if params['status'] not in STATUSES:
                raise ValidationError({'status': f'One of {", ".join(sorted(STATUSES))}.'})
            queryset = queryset.filter(status=params['status'])
        if params.get('supplier'):
            try:
                queryset = queryset.filter(supplier_id=int(params['supplier']))
            except ValueError:
                raise ValidationError({'supplier': 'A supplier id.'}) from None
        return queryset

    def _answer(self, order):
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data)

    def _locked(self, statuses):
        order = self.get_object()
        with transaction.atomic():
            order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
            if order.status not in statuses:
                raise ValidationError({'detail': f'This order is {order.get_status_display().lower()}.'})
        return order

    @extend_schema(request=None, responses=DraftSummarySerializer)
    @action(detail=False, methods=['post'])
    def draft(self, request):
        """Draft orders now (staff), as the nightly run does."""
        return Response(DraftSummarySerializer(draft_orders(request.user.company, user=request.user)).data)

    @extend_schema(request=LineEditSerializer, responses=PurchaseOrderSerializer)
    @action(detail=True, methods=['patch'], url_path=r'lines/(?P<line_id>\d+)', url_name='line')
    def line(self, request, pk=None, line_id=None):
        """Change a draft line's packs (staff); 0 takes it out. The order is then edited: runs leave it alone."""
        body = LineEditSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        with transaction.atomic():
            order = self._locked([PurchaseOrder.DRAFT])
            line = get_object_or_404(PurchaseOrderLine, pk=line_id, order=order)
            if body.validated_data['packs'] == 0:
                line.delete()
            else:
                line.packs = body.validated_data['packs']
                line.line_cost = (line.pack_cost or 0) * line.packs
                line.save(update_fields=['packs', 'line_cost'])
            order.edited = True
            order.total_cost = sum(item.line_cost for item in order.lines.all())
            order.save(update_fields=['edited', 'total_cost'])
        return self._answer(order)

    @extend_schema(request=None, responses=PurchaseOrderSerializer)
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a draft (staff). It counts as on order until it is due; it is not sent from here."""
        with transaction.atomic():
            order = self._locked([PurchaseOrder.DRAFT])
            if not order.lines.exists():
                raise ValidationError({'detail': 'This order has no lines.'})
            order.status, order.approved_by, order.approved_at = PurchaseOrder.APPROVED, request.user, timezone.now()
            order.expected_on = uk_date() + timedelta(days=order.supplier.lead_time_days)
            order.save(update_fields=['status', 'approved_by', 'approved_at', 'expected_on'])
        return self._answer(order)

    @extend_schema(request=None, responses=PurchaseOrderSerializer)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a draft or an approved order (staff)."""
        with transaction.atomic():
            order = self._locked([PurchaseOrder.DRAFT, PurchaseOrder.APPROVED])
            order.status = PurchaseOrder.CANCELLED
            order.save(update_fields=['status'])
        return self._answer(order)
