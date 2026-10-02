from django.contrib import admin

from .models import Order, Product, ProductCategory, StockBatch, StockMovement, Supplier

admin.site.register(ProductCategory)
admin.site.register(Order)
admin.site.register(Supplier)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'price', 'stock_quantity', 'reorder_threshold')
    search_fields = ['name', 'commodity_code']
    list_filter = ['category']


@admin.register(StockBatch)
class StockBatchAdmin(admin.ModelAdmin):
    list_display = ('batch_number', 'product', 'quantity', 'expiration_date')
    search_fields = ['batch_number', 'product__name']
    list_filter = ['expiration_date']


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('id', 'batch', 'movement_type', 'quantity', 'date_time')
    search_fields = ['batch__batch_number']
    list_filter = ['movement_type', 'date_time']
