# inventory/views.py

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from common.tenancy import SharedReferenceMixin, TenantScopedMixin

from .models import ProductCategory, Product, Order, Supplier, StockBatch, StockMovement
from .projections import project_stock
from .serializers import (
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


class StockMovementViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = StockMovement.objects.all()
    serializer_class = StockMovementSerializer
