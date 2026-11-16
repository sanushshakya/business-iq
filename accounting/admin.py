from django.contrib import admin

from common.tenancy import TenantAdminMixin
from inventory.admin import ViewOnlyAdmin

from .models import Account, HmrcSubmission, Journal, JournalLine, Period, PeriodEvent, VatReturn


@admin.register(Account, Journal, JournalLine, Period, PeriodEvent, VatReturn, HmrcSubmission)
class AccountingAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only (G52): accounts are kept, journals posted and reversed, periods locked through the API."""
