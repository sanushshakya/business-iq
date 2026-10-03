from django.contrib import admin

from common.tenancy import TenantAdminMixin

# Import models from tenants app
from .models import Company, Branch, Till

# Register models in Django admin with list_display showing parent relationships

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Company model.
    """
    list_display = ('name', 'registration_number')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(pk=request.user.company_id)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

@admin.register(Branch)
class BranchAdmin(TenantAdminMixin, admin.ModelAdmin):
    """
    Admin configuration for the Branch model.
    """
    list_display = ('name', 'company', 'address', 'is_active')

@admin.register(Till)
class TillAdmin(TenantAdminMixin, admin.ModelAdmin):
    """
    Admin configuration for the Till model.
    """
    list_display = ('branch', 'number')