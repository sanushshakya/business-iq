from django.contrib import admin

from common.tenancy import TenantAdminMixin

from .models import Sale, SaleAllocation, SaleItem, TillSession


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
    list_display = ('id', 'session', 'cashier', 'payment_method', 'total', 'status', 'created_at')
    list_filter = ['status', 'payment_method']
    list_select_related = ('session__till', 'cashier')


@admin.register(SaleItem)
class SaleItemAdmin(ReadOnlyAdmin):
    list_display = ('sale', 'product_name', 'quantity', 'unit_price', 'line_total')


@admin.register(SaleAllocation)
class SaleAllocationAdmin(ReadOnlyAdmin):
    list_display = ('item', 'batch', 'quantity')
