from django.contrib import admin

# Register your models here.

from .models import StockBatch, StockMovement

@admin.register(StockBatch)
class StockBatchAdmin(admin.ModelAdmin):
    """
    Admin class for the StockBatch model.
    """

    list_display = ('batch_id', 'product_name', 'quantity', 'expiry_date')
    search_fields = ['batch_id', 'product_name']
    list_filter = ['expiry_date']

@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    """
    Admin class for the StockMovement model.
    """

    list_display = ('movement_id', 'batch', 'type', 'quantity', 'timestamp')
    search_fields = ['movement_id', 'type']
    list_filter = ['type', 'timestamp']
