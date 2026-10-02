# inventory/views.py

from rest_framework import viewsets

from common.tenancy import IsStaffOrReadOnly, TenantScopedMixin

from .models import ProductCategory, Product, Order, Supplier, StockBatch, StockMovement
from .serializers import ProductCategorySerializer, ProductSerializer, OrderSerializer, SupplierSerializer, StockBatchSerializer, StockMovementSerializer


class ProductCategoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsStaffOrReadOnly]
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer


class ProductViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer


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
