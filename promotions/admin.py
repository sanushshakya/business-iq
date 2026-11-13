from django.contrib import admin

from common.tenancy import TenantAdminMixin
from inventory.admin import ViewOnlyAdmin

from .models import Promotion, PromotionGroup


@admin.register(Promotion, PromotionGroup)
class PromotionAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only (G58): promotions are kept through the API and the Promotions page."""
