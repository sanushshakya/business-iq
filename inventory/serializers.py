# inventory/serializers.py

from rest_framework import serializers

from common.tenancy import TenantModelSerializer

from .models import ProductCategory, Product, Order, Supplier, StockBatch, StockMovement


class ProductCategorySerializer(TenantModelSerializer):
    class Meta:
        model = ProductCategory
        fields = '__all__'


class ProductSerializer(TenantModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'


class OrderSerializer(TenantModelSerializer):
    class Meta:
        model = Order
        fields = '__all__'


class SupplierSerializer(TenantModelSerializer):
    class Meta:
        model = Supplier
        fields = '__all__'


class StockBatchSerializer(TenantModelSerializer):
    class Meta:
        model = StockBatch
        fields = '__all__'


class StockMovementSerializer(TenantModelSerializer):
    class Meta:
        model = StockMovement
        fields = '__all__'
