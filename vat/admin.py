from django.contrib import admin

from .models import VatRate
from .services import uk_date


@admin.register(VatRate)
class VatRateAdmin(admin.ModelAdmin):
    """
    National rates: staff can look, only superusers can change them (a change applies to every company). A rate
    already in force is history and is locked: a new rate is a new row with a later ``valid_from``.
    """

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
        return self.has_add_permission(request) and not _in_force(obj)

    def get_readonly_fields(self, request, obj=None):
        return ('code', 'rate', 'valid_from') if _in_force(obj) else ()

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)  # bulk delete would skip the per-row check above
        return actions


def _in_force(rate):
    return rate is not None and rate.valid_from <= uk_date()
