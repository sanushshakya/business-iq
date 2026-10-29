from django.contrib import admin

from .models import VatRate


@admin.register(VatRate)
class VatRateAdmin(admin.ModelAdmin):
    """National rates: staff can look, only superusers can change them (a change applies to every company)."""

    list_display = ('code', 'rate', 'valid_from')
    list_filter = ('code',)

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def has_module_permission(self, request):
        return self.has_view_permission(request)

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return self.has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_add_permission(request)
