from django.contrib import admin

from common.tenancy import TenantAdminMixin
from inventory.admin import ViewOnlyAdmin

from .models import PurchaseOrder, PurchaseOrderLine, SupplierProduct


@admin.register(SupplierProduct, PurchaseOrder, PurchaseOrderLine)
class PurchasingAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only (G57): supplier products and orders are kept through the API."""
