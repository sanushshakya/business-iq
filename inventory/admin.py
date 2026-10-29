from django.contrib import admin

from common.tenancy import TenantAdminMixin

from .models import Location, Product, ProductCategory, StockBatch, StockMovement


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
    list_select_related = ('product',)


@admin.register(StockMovement)
class StockMovementAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'batch', 'movement_type', 'quantity', 'date_time')
    search_fields = ['batch__batch_number']
    list_filter = ['movement_type', 'date_time']
    list_select_related = ('batch__product',)

    # The ledger is append-only: editing it here would leave batch and product stock out of step.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Location)
class LocationAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Company locations. The virtual ones (supplier, customer, waste, ...) are the system's: view only."""

    list_display = ('code', 'name', 'type', 'branch', 'sellable', 'online_sellable', 'is_active')
    list_filter = ('type', 'sellable', 'is_active')
    search_fields = ('code', 'name')
    list_select_related = ('branch',)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.is_virtual:
            return ('code', 'name', 'type', 'branch', 'sellable', 'online_sellable', 'is_active')
        return ()

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj is not None and obj.is_virtual)
