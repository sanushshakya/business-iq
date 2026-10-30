# inventory/views.py

from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from common.tenancy import SharedReferenceMixin, TenantScopedMixin
from tenants.views import IsCompanyStaff

from . import transfers
from .models import Location, ProductCategory, Product, Order, Supplier, StockBatch, StockMovement, Transfer
from .projections import project_stock
from .serializers import (
    LocationSerializer,
    OrderSerializer,
    ProductCategorySerializer,
    ProductSerializer,
    StockBatchSerializer,
    StockMovementSerializer,
    StockProjectionSerializer,
    SupplierSerializer,
    TransferReceiveSerializer,
    TransferSerializer,
)


class ProductCategoryViewSet(SharedReferenceMixin, viewsets.ModelViewSet):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer


class ProductViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer

    @extend_schema(
        parameters=[OpenApiParameter('days', int, description='How many days ahead to project (1-180, default 30).')],
        responses=StockProjectionSerializer,
    )
    @action(detail=True, methods=['get'], url_path='stock-projection')
    def stock_projection(self, request, pk=None):
        """Projected stock by day, from recent sales and the demand calendar, with the expected stock-out date."""
        try:
            days = int(request.query_params.get('days', 30))
        except ValueError:
            days = 0
        if not 1 <= days <= 180:
            return Response({'days': ['Must be a whole number between 1 and 180.']}, status=400)
        return Response(StockProjectionSerializer(project_stock(self.get_object(), days)).data)


class OrderViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer


class SupplierViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer


class StockBatchViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = StockBatch.objects.all()
    serializer_class = StockBatchSerializer


class StockMovementViewSet(
    TenantScopedMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Append-only stock ledger: creating a movement updates the batch and product stock."""

    queryset = StockMovement.objects.all()
    serializer_class = StockMovementSerializer


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('type', str, description='Only this type, e.g. warehouse.'),
    OpenApiParameter('branch', int, description='Only locations in this branch.'),
    OpenApiParameter('sellable', bool, description='true: only sellable locations; false: only the others.'),
]))
class LocationViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """
    Stock locations (company staff only): shop floors, butchery, warehouses, back stores, vans, plus the virtual
    supplier/customer/waste/adjustment/in-transit locations every company has. Retire one with ``is_active``.
    """

    queryset = Location.objects.select_related('branch')
    serializer_class = LocationSerializer
    permission_classes = [IsCompanyStaff]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if params.get('type'):
            queryset = queryset.filter(type=params['type'])
        if params.get('branch', '').isdigit():
            queryset = queryset.filter(branch_id=params['branch'])
        if params.get('sellable') in ('true', 'false'):
            queryset = queryset.filter(sellable=params['sellable'] == 'true')
        return queryset

    def perform_destroy(self, instance):
        if instance.is_virtual:
            raise ValidationError({'detail': 'Virtual locations are managed by the system and cannot be removed.'})
        instance.delete()


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('status', str, description='Only transfers in this status (draft, dispatched, received, cancelled).'),
]))
class TransferViewSet(
    TenantScopedMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    """
    Stock transfers between the company's own locations: draft → dispatched (stock goes to IN-TRANSIT) → received
    (what arrived goes to the destination, any shortfall to ADJUSTMENT with a reason), or draft → cancelled.
    Transfers are never deleted.
    """

    queryset = Transfer.objects.prefetch_related('lines')
    serializer_class = TransferSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('status'):
            queryset = queryset.filter(status=self.request.query_params['status'])
        return queryset

    def _done(self, step, *args):
        try:
            transfer = step(self.get_object(), self.request.user, *args)
        except transfers.TransferError as error:
            raise ValidationError(error.detail)
        return Response(TransferSerializer(self.get_queryset().get(pk=transfer.pk), context=self.get_serializer_context()).data)

    @extend_schema(request=None, responses=TransferSerializer)
    @action(detail=True, methods=['post'], url_path='dispatch', url_name='dispatch')
    def dispatch_transfer(self, request, pk=None):
        """Send the stock: each line is taken from the source (earliest expiry first) to IN-TRANSIT."""
        return self._done(transfers.dispatch)

    @extend_schema(request=TransferReceiveSerializer, responses=TransferSerializer)
    @action(detail=True, methods=['post'])
    def receive(self, request, pk=None):
        """Book what arrived, once, for every line; a difference needs a reason and goes to ADJUSTMENT."""
        serializer = TransferReceiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._done(transfers.receive, serializer.validated_data['lines'])

    @extend_schema(request=None, responses=TransferSerializer)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a draft. A dispatched transfer is closed by receiving it (0 if nothing arrived)."""
        return self._done(transfers.cancel)
