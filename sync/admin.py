from django.contrib import admin

from common.tenancy import TenantAdminMixin

from .models import ShopifyConnection, SyncTask


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
