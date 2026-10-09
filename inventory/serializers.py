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


class StockProjectionPointSerializer(serializers.Serializer):
    date = serializers.DateField()
    projected_quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    expected_demand = serializers.DecimalField(max_digits=12, decimal_places=2)
    demand_multiplier = serializers.DecimalField(max_digits=5, decimal_places=2)


class StockProjectionSerializer(serializers.Serializer):
    """Response of ``/inventory/products/{id}/stock-projection/``."""

    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    current_stock = serializers.IntegerField()
    reorder_threshold = serializers.IntegerField()
    average_daily_demand = serializers.DecimalField(max_digits=12, decimal_places=2)
    days_of_cover = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    stockout_date = serializers.DateField(allow_null=True)
    reorder_date = serializers.DateField(allow_null=True)
    projection = StockProjectionPointSerializer(many=True)
