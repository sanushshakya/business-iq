from django.contrib import admin

from common.tenancy import TenantAdminMixin

from .models import Location, Product, ProductCategory, StockBatch, StockMovement, Transfer, TransferLine, TransferPick


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
    list_display = ('batch_number', 'product', 'location', 'quantity', 'expiration_date')
    search_fields = ['batch_number', 'product__name']
    list_filter = ['location', 'expiration_date']
    list_select_related = ('product', 'location')

    def get_readonly_fields(self, request, obj=None):
        # Changing a batch's location would move stock without a movement; that is a transfer (G11).
        return ('location',) if obj is not None else ()


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


class TransferLineInline(admin.TabularInline):
    model = TransferLine
    fields = ('product', 'quantity', 'received_quantity', 'difference_reason')
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class ViewOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TransferLine, TransferPick)
class TransferPartAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """A line or pick changed by hand would no longer match the stock its transfer moved."""


@admin.register(Transfer)
class TransferAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only: transfers move stock, so they change only through the API (dispatch, receive, cancel)."""

    list_display = ('reference', 'source_location', 'destination_location', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('number', 'note')
    list_select_related = ('source_location', 'destination_location')
    inlines = [TransferLineInline]
