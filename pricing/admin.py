from django.contrib import admin

from common.tenancy import TenantAdminMixin

from .models import PriceChangeLog, PricingPlan


@admin.register(PricingPlan)
class PricingPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price_per_month')
    search_fields = ('name',)


@admin.register(PriceChangeLog)
class PriceChangeLogAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Approval changes the product price, so it happens through the API's approve action, not by ticking a box."""

    list_display = ('product', 'old_price', 'new_price', 'reason', 'is_approved', 'is_processed', 'changed_at')
    list_filter = ('is_approved', 'is_processed')
    search_fields = ('product__name',)
    list_select_related = ('product',)
    readonly_fields = ('old_price', 'is_approved', 'is_processed', 'changed_at')
