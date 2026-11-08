from django import forms
from django.contrib import admin
from django.forms.models import BaseInlineFormSet

from common.tenancy import TenantAdminMixin
from inventory.admin import ViewOnlyAdmin

from .models import (
    CreditNote,
    CreditNoteLine,
    CreditOverride,
    CustomerGroup,
    CustomerReturn,
    DeliveryRun,
    DeliveryStop,
    Invoice,
    Allocation,
    LedgerEntry,
    Payment,
    InvoiceLine,
    PriceList,
    PriceListEntry,
    ReturnLine,
    SalesOrder,
    SalesOrderLine,
    SalesOrderPick,
    TradeCustomer,
    TradeCustomerAddress,
    TradeCustomerContact,
    min_quantity_problem,
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
                group = cleaned.get('group')
                if group is not None and group.company_id != company_id:  # superusers' choices are not scoped
                    self.add_error('group', 'This group belongs to another company.')
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


@admin.register(CustomerGroup)
class CustomerGroupAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Customer groups (G39). Deleting one leaves its customers with no group."""

    list_display = ('name',)
    search_fields = ('name',)


class PriceListEntryForm(forms.ModelForm):
    def clean(self):
        cleaned = super().clean()
        product, min_quantity = cleaned.get('product'), cleaned.get('min_quantity')
        if product is not None and min_quantity is not None:
            problem = min_quantity_problem(product, min_quantity)
            if problem:
                self.add_error('min_quantity', problem)
        return cleaned


def _list_company_id(request, form):
    """The company a price list will belong to: chosen in the form (superusers), kept, or the user's own."""
    company = form.cleaned_data.get('company') if hasattr(form, 'cleaned_data') else None
    return company.pk if company else (form.instance.company_id or request.user.company_id)


class PriceListEntryFormSet(BaseInlineFormSet):
    company_id = None  # set per request by the inline

    def clean(self):
        super().clean()
        company_id = self.instance.company_id or self.company_id
        for form in self.forms:
            product = form.cleaned_data.get('product') if form.cleaned_data else None
            if product is not None and not form.cleaned_data.get('DELETE') and product.company_id != company_id:
                form.add_error('product', 'This product belongs to another company.')


class PriceListEntryInline(admin.TabularInline):
    model = PriceListEntry
    form = PriceListEntryForm
    formset = PriceListEntryFormSet
    extra = 0
    raw_id_fields = ('product',)  # catalogues are large

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == 'product' and not request.user.is_superuser and request.user.company_id:
            field.queryset = field.queryset.filter(company_id=request.user.company_id)
        return field

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        return type('ScopedEntryFormSet', (formset,), {'company_id': request.user.company_id})


@admin.register(PriceList)
class PriceListAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Price lists (G39), ex VAT. Highest priority wins where lists overlap, then the newest."""

    list_display = ('name', 'priority', 'is_active', 'valid_from', 'valid_to')
    list_filter = ('is_active',)
    search_fields = ('name',)
    filter_horizontal = ('customers', 'groups')
    inlines = [PriceListEntryInline]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        class ScopedForm(form):
            def clean(self):
                cleaned = super().clean()
                company_id = _list_company_id(request, self)
                for name in ('customers', 'groups'):  # superusers' choices are not scoped
                    if any(row.company_id != company_id for row in cleaned.get(name) or []):
                        self.add_error(name, "Another company's rows can't be on this list.")
                return cleaned

        return ScopedForm


@admin.register(PriceListEntry)
class PriceListEntryAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """Changed on the price list's page only."""


class SalesOrderLineInline(admin.TabularInline):
    model = SalesOrderLine
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SalesOrder)
class SalesOrderAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only: orders change through the API, which keeps the status machine and the price snapshot (G40)."""

    list_display = ('number_display', 'customer', 'delivery_date', 'status', 'estimated_gross')
    list_filter = ('status', 'delivery_date')
    search_fields = ('number', 'customer__account_code', 'customer__legal_name', 'customer_ref')
    list_select_related = ('customer',)
    inlines = [SalesOrderLineInline]


@admin.register(SalesOrderLine)
class SalesOrderLineAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """Changed through the order only."""


@admin.register(SalesOrderPick)
class SalesOrderPickAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only: a finished pick is the record of what moved to dispatch (G42)."""

    list_display = ('line', 'batch', 'quantity', 'weight_kg', 'picked_at')
    list_select_related = ('line__product', 'batch')


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only: invoices are made and issued through the API; an issued one never changes (G43)."""

    list_display = ('number_display', 'customer_name', 'status', 'invoice_date', 'gross_total', 'email_status')
    list_filter = ('status', 'email_status')
    search_fields = ('number', 'customer_code', 'customer_name')
    inlines = [InvoiceLineInline]


@admin.register(InvoiceLine)
class InvoiceLineAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """Changed through the invoice only."""


class DeliveryStopInline(admin.TabularInline):
    model = DeliveryStop
    extra = 0
    can_delete = False
    exclude = ('photo',)  # fetched through the run's endpoint only

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DeliveryRun)
class DeliveryRunAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only: runs move stock, so they change through the API (start, deliver, complete)."""

    list_display = ('number_display', 'run_date', 'vehicle', 'driver', 'status')
    list_filter = ('status', 'run_date')
    inlines = [DeliveryStopInline]


@admin.register(DeliveryStop)
class DeliveryStopAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """Changed through the run only."""

    exclude = ('photo',)


@admin.register(CustomerReturn, ReturnLine, CreditNote, CreditNoteLine)
class ReturnsAndCreditsAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only: returns move stock and credit notes are frozen once issued (G45); both change through the API."""


@admin.register(LedgerEntry)
class LedgerEntryAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only: the customer ledger is append-only (G47); adjustments are posted through the API."""

    list_display = ['customer', 'entry_date', 'kind', 'amount', 'description']
    list_filter = ['kind']


@admin.register(CreditOverride)
class CreditOverrideAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only: a credit override is a logged decision (G48), made through the API."""

    list_display = ['order', 'created_at', 'created_by', 'blocks', 'exposure', 'credit_limit']


@admin.register(Payment, Allocation)
class PaymentsAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only: payments are reversed and allocations released through the API (G49), never edited."""
