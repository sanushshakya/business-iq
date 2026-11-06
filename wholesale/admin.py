from django import forms
from django.contrib import admin
from django.forms.models import BaseInlineFormSet

from common.tenancy import TenantAdminMixin
from inventory.admin import ViewOnlyAdmin

from .models import TradeCustomer, TradeCustomerAddress, TradeCustomerContact, normalise_account_code


class _OneFlagged(BaseInlineFormSet):
    flag = ''
    message = ''

    def clean(self):
        super().clean()
        chosen = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get('DELETE')
                  and f.cleaned_data.get(self.flag)]
        if len(chosen) > 1:
            raise forms.ValidationError(self.message)


class AddressFormSet(_OneFlagged):
    flag, message = 'is_default', 'Only one default address.'


class ContactFormSet(_OneFlagged):
    flag, message = 'is_primary', 'Only one primary contact.'


class AddressInline(admin.StackedInline):
    model = TradeCustomerAddress
    formset = AddressFormSet
    extra = 0


class ContactInline(admin.TabularInline):
    model = TradeCustomerContact
    formset = ContactFormSet
    extra = 0


@admin.register(TradeCustomer)
class TradeCustomerAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Trade customers (G38). The account code is unique per company; close a customer rather than delete it."""

    list_display = ('account_code', 'legal_name', 'trading_name', 'status', 'vat_number')
    list_filter = ('status',)
    search_fields = ('account_code', 'legal_name', 'trading_name')
    inlines = [AddressInline, ContactInline]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        company_id = getattr(obj, 'company_id', None) or request.user.company_id

        class ScopedForm(form):
            def clean(self):
                cleaned = super().clean()
                code = normalise_account_code(cleaned.get('account_code'))  # as the model will store it
                taken = TradeCustomer.objects.filter(company_id=company_id, account_code=code)
                if code and taken.exclude(pk=self.instance.pk).exists():
                    self.add_error('account_code', 'Another customer already has this account code.')
                return cleaned

        return ScopedForm

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TradeCustomerAddress)
class TradeCustomerAddressAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """Changed on the customer's page only (one default per customer)."""


@admin.register(TradeCustomerContact)
class TradeCustomerContactAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """Changed on the customer's page only (one primary per customer)."""
