from django.contrib import admin

from common.tenancy import TenantAdminMixin

from .models import InboundEvent, ShopifyConnection, StorefrontConnection, SyncTask


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

    list_display = ('name', 'company', 'base_url', 'is_active', 'created_at')

    def get_exclude(self, request, obj=None):
        return ('webhook_secret',) if obj else ()


@admin.register(InboundEvent)
class InboundEventAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Webhooks received from the storefront. Read-only: they record what was applied to stock."""

    list_display = ('event_type', 'event_id', 'status', 'connection', 'received_at')
    list_filter = ('status', 'event_type')
    search_fields = ('event_id',)
    readonly_fields = ('connection', 'event_id', 'event_type', 'status', 'detail', 'payload', 'received_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
