from django.contrib import admin
from django.db import transaction

from common.tenancy import TenantAdminMixin

from .models import (
    InboundEvent, OutboundEvent, ShopifyConnection, StockReconciliation, StockSyncState, StorefrontConnection, SyncTask,
)


@admin.register(SyncTask)
class SyncTaskAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('task_id', 'source_system', 'target_system', 'status', 'start_time', 'end_time')
    list_filter = ('status',)
    search_fields = ('task_id',)
    readonly_fields = ('task_id', 'status', 'start_time', 'end_time')


@admin.register(ShopifyConnection)
class ShopifyConnectionAdmin(TenantAdminMixin, admin.ModelAdmin):
    """The access token can be entered when connecting a store but is never displayed afterwards."""

    list_display = ('shop_domain', 'company', 'created_at')
    search_fields = ('shop_domain',)

    def get_exclude(self, request, obj=None):
        return ('access_token',) if obj else ()


@admin.register(StorefrontConnection)
class StorefrontConnectionAdmin(TenantAdminMixin, admin.ModelAdmin):
    """The webhook secret can be entered when connecting the storefront but is never displayed afterwards."""

    list_display = ('name', 'company', 'base_url', 'is_active', 'push_prices', 'created_at')

    def get_exclude(self, request, obj=None):
        return ('webhook_secret',) if obj else ()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.push_prices and 'push_prices' in form.changed_data:
            # Prices only travel with a product's snapshot: queue them all so every price goes out on the next sweep.
            from .stock import mark_all

            company_id = obj.company_id
            transaction.on_commit(lambda: mark_all(company_id))


@admin.register(InboundEvent)
class InboundEventAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Webhooks received from the storefront. Read-only and never deleted: each row is what stops a redelivered
    order from being applied to stock twice."""

    list_display = ('event_type', 'event_id', 'status', 'connection', 'received_at')
    list_filter = ('status', 'event_type')
    search_fields = ('event_id',)
    readonly_fields = ('connection', 'event_id', 'event_type', 'status', 'detail', 'payload', 'received_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OutboundEvent)
class OutboundEventAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Events sent to the storefront (loyalty awards and reversals). Read-only: the sweep owns their state."""

    list_display = ('event_type', 'event_id', 'status', 'attempts', 'next_attempt_at', 'connection', 'created_at')
    list_filter = ('status', 'event_type')
    search_fields = ('event_id',)
    readonly_fields = (
        'connection', 'event_id', 'event_type', 'status', 'attempts', 'next_attempt_at', 'last_error', 'payload',
        'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(TenantAdminMixin, admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockSyncState)
class StockSyncStateAdmin(ReadOnlyAdmin):
    """Online stock last sent to the storefront per product; ``pending`` ones go out within ~15 s."""

    list_display = ('product', 'pending', 'last_sent_available', 'version', 'changed_at')
    list_filter = ('pending',)
    search_fields = ('product__name', 'product__external_id')


@admin.register(StockReconciliation)
class StockReconciliationAdmin(ReadOnlyAdmin):
    """Nightly stock comparison with the storefront. Mismatches are fixed by the resend that follows each run."""

    list_display = ('ran_at', 'connection', 'checked', 'mismatch_count', 'error')
    readonly_fields = ('connection', 'ran_at', 'checked', 'mismatches', 'error')

    @admin.display(description='Mismatches')
    def mismatch_count(self, obj):
        return len(obj.mismatches)
