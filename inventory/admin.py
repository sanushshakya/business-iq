from django.contrib import admin

from common.tenancy import TenantAdminMixin

from .models import Product, ProductCategory, StockBatch, StockMovement


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    """Shared reference data: only staff can edit it through the API, and the admin follows suit."""

    search_fields = ['name']


@admin.register(Product)
class ProductAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'category', 'price', 'stock_quantity', 'reorder_threshold')
    search_fields = ['name', 'commodity_code']
    list_filter = ['category']


@admin.register(StockBatch)
class StockBatchAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('batch_number', 'product', 'quantity', 'expiration_date')
    search_fields = ['batch_number', 'product__name']
    list_filter = ['expiration_date']


@admin.register(StockMovement)
class StockMovementAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'batch', 'movement_type', 'quantity', 'date_time')
    search_fields = ['batch__batch_number']
    list_filter = ['movement_type', 'date_time']
