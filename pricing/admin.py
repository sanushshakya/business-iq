from django.contrib import admin

from common.tenancy import TenantAdminMixin

from .models import PriceChangeLog, PricingPlan


@admin.register(PricingPlan)
class PricingPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price_per_month')
    search_fields = ('name',)


@admin.register(PriceChangeLog)
class PriceChangeLogAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Approval changes the product price, so deciding happens through the API's approve/reject actions, not here."""

    list_display = (
        'product', 'old_price', 'new_price', 'reason', 'source', 'is_approved', 'is_rejected', 'decided_by', 'changed_at'
    )
    list_filter = ('source', 'is_approved', 'is_rejected', 'is_processed')
    search_fields = ('product__name',)
    list_select_related = ('product',)
    readonly_fields = (
        'old_price', 'source', 'requested_by', 'is_approved', 'is_rejected', 'decided_by', 'decided_at', 'decision_note',
        'is_processed', 'changed_at',
    )
