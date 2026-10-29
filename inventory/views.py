# inventory/views.py

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from common.tenancy import SharedReferenceMixin, TenantScopedMixin
from tenants.views import IsCompanyStaff

from .models import Location, ProductCategory, Product, Order, Supplier, StockBatch, StockMovement
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


@extend_schema(parameters=[
    OpenApiParameter('type', str, description='Only this type, e.g. warehouse.'),
    OpenApiParameter('branch', int, description='Only locations in this branch.'),
    OpenApiParameter('sellable', bool, description='true: only sellable locations; false: only the others.'),
])
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
