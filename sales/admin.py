from django.contrib import admin

from common.tenancy import TenantAdminMixin

from .models import Sale, SaleAllocation, SaleException, SaleItem, TillSession


class ReadOnlyAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Till records are a financial ledger: look, don't touch. Voids and cash-ups go through the API."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TillSession)
class TillSessionAdmin(ReadOnlyAdmin):
    list_display = ('id', 'till', 'opened_by', 'opened_at', 'closed_at', 'expected_cash', 'counted_cash')
    list_select_related = ('till__branch', 'opened_by')


@admin.register(Sale)
class SaleAdmin(ReadOnlyAdmin):
    list_display = ('id', 'session', 'cashier', 'payment_method', 'total', 'status', 'created_at', 'offline_number')
    list_filter = ['status', 'payment_method']
    search_fields = ['offline_number']
    list_select_related = ('session__till', 'cashier')


@admin.register(SaleItem)
class SaleItemAdmin(ReadOnlyAdmin):
    list_display = ('sale', 'product_name', 'quantity', 'weight_kg', 'unit_price', 'line_total', 'label_price',
                    'expected_price', 'label_price_flagged', 'manual_weight')
    # Scanned labels charged at a price far from today's (G18) and typed weights (G19), for a manager to look at.
    list_filter = ['label_price_flagged', 'manual_weight']
    search_fields = ['product_name', 'barcode']


@admin.register(SaleAllocation)
class SaleAllocationAdmin(ReadOnlyAdmin):
    list_display = ('item', 'batch', 'quantity')


@admin.register(SaleException)
class SaleExceptionAdmin(ReadOnlyAdmin):
    """Offline sales that didn't match the server when they were synced (G28); the review screen is G29."""

    list_display = ('sale', 'kind', 'product', 'expected', 'charged', 'quantity', 'weight_kg', 'created_at',
                    'resolved_at')
    list_filter = ['kind']
    list_select_related = ('sale', 'product')
