from django import forms
from django.contrib import admin
from django.forms.models import BaseInlineFormSet

from common.tenancy import TenantAdminMixin
from inventory.admin import ViewOnlyAdmin

from .models import (
    TradeCustomer,
    TradeCustomerAddress,
    TradeCustomerContact,
    normalise_account_code,
    normalise_postcode,
    normalise_vat_number,
)


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


class OneFlaggedForm(forms.ModelForm):
    """The models' only constraints are "one default address" / "one primary contact". Each form would check them
    against the rows as saved, so moving the flag from A to B could never pass in one save; the formset checks them
    across all rows instead, and the rows are saved in order (the old default first)."""

    def validate_constraints(self):
        pass


class AddressForm(OneFlaggedForm):
    postcode = forms.CharField(max_length=12)  # wider than stored: spaces go before the length counts

    def clean_postcode(self):
        return normalise_postcode(self.cleaned_data['postcode'])


class AddressInline(admin.StackedInline):
    model = TradeCustomerAddress
    form = AddressForm
    formset = AddressFormSet
    extra = 0


class ContactInline(admin.TabularInline):
    model = TradeCustomerContact
    form = OneFlaggedForm
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

        class ScopedForm(form):
            # Wider than stored: people type spaces, which go before the length counts.
            vat_number = forms.CharField(max_length=24, required=False)

            def clean_vat_number(self):
                return normalise_vat_number(self.cleaned_data['vat_number'])

            def clean(self):
                cleaned = super().clean()
                # The company the row will belong to: chosen in the form (superusers), kept, or the user's own.
                company = cleaned.get('company')
                company_id = company.pk if company else (obj.company_id if obj else request.user.company_id)
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
