from django.contrib import admin

# Import models from tenants app
from .models import Company, Branch, Till

# Register models in Django admin with list_display showing parent relationships

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Company model.
    """
    list_display = ('name', 'code')

@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Branch model.
    """
    list_display = ('name', 'company', 'location')

@admin.register(Till)
class TillAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Till model.
    """
    list_display = ('branch', 'number')