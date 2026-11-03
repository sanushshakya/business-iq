from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from common.tenancy import TenantAdminMixin

from .models import (
    BarcodeRule, Location, Product, ProductCategory, StockBatch, StockMovement, Transfer, TransferLine, TransferPick,
    Requisition, RequisitionLine, Breakdown, BreakdownLine, WriteOff, YieldTemplate, YieldTemplateOutput, other_company_problem, weighed_product_problem,
    yield_output_problems,
)


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    """Shared reference data: only staff can edit it through the API, and the admin follows suit."""

    search_fields = ['name']


@admin.register(Product)
class ProductAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'category', 'price', 'stock_quantity', 'reorder_threshold')
    search_fields = ['name', 'commodity_code', 'barcode', 'plu']
    list_filter = ['category']


@admin.register(StockBatch)
class StockBatchAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('batch_number', 'product', 'location', 'quantity', 'expiration_date')
    search_fields = ['batch_number', 'product__name']
    list_filter = ['location', 'expiration_date']
    list_select_related = ('product', 'location')

    def get_readonly_fields(self, request, obj=None):
        # Changing a batch's location would move stock without a movement; that is a transfer (G11).
        return ('location',) if obj is not None else ()


@admin.register(StockMovement)
class StockMovementAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'batch', 'movement_type', 'quantity', 'date_time')
    search_fields = ['batch__batch_number']
    list_filter = ['movement_type', 'date_time']
    list_select_related = ('batch__product',)

    # The ledger is append-only: editing it here would leave batch and product stock out of step.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Location)
class LocationAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Company locations. The virtual ones (supplier, customer, waste, ...) are the system's: view only."""

    list_display = ('code', 'name', 'type', 'branch', 'sellable', 'online_sellable', 'is_active')
    list_filter = ('type', 'sellable', 'is_active')
    search_fields = ('code', 'name')
    list_select_related = ('branch',)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.is_virtual:
            return ('code', 'name', 'type', 'branch', 'sellable', 'online_sellable', 'is_active')
        return ()

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj is not None and obj.is_virtual)


class TransferLineInline(admin.TabularInline):
    model = TransferLine
    fields = ('product', 'quantity', 'received_quantity', 'difference_reason')
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class ViewOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TransferLine, TransferPick, RequisitionLine)
class TransferPartAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """A transfer line or pick (or a requisition line) changed by hand would no longer match its document."""


@admin.register(Transfer)
class TransferAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only: transfers move stock, so they change only through the API (dispatch, receive, cancel)."""

    list_display = ('reference', 'source_location', 'destination_location', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('number', 'note')
    list_select_related = ('source_location', 'destination_location')
    inlines = [TransferLineInline]


@admin.register(Requisition)
class RequisitionAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """View only: requisitions are raised, fulfilled and cancelled through the API."""

    list_display = ('reference', 'location', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('number', 'note')
    list_select_related = ('location',)


@admin.register(BarcodeRule)
class BarcodeRuleAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Scale-label layouts (G15); the layout is checked on save (``BarcodeRule.clean``)."""

    list_display = ('prefix', 'name', 'value_type', 'item_code_length', 'decimals', 'value_check_digit', 'is_active')
    list_filter = ('value_type', 'is_active')


class YieldOutputFormSet(BaseInlineFormSet):
    """The API's rules for a template's outputs (``yield_output_problems``), on the admin's inline rows."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        rows = [form.cleaned_data for form in self.forms
                if form.cleaned_data and not form.cleaned_data.get('DELETE')]
        input_product = getattr(self.instance, 'input_product', None) if self.instance.input_product_id else None
        problem = weighed_product_problem(input_product)
        problems = ([problem] if problem else []) + yield_output_problems(input_product, rows)
        problems += [p for p in (other_company_problem(row.get('product'), self.instance.company_id) for row in rows) if p]
        if problems:
            raise ValidationError(problems)


class YieldTemplateOutputInline(TenantAdminMixin, admin.TabularInline):
    model = YieldTemplateOutput
    formset = YieldOutputFormSet
    fields = ('position', 'kind', 'product', 'name', 'expected_percent', 'keeps_days')
    extra = 0


@admin.register(YieldTemplateOutput)
class YieldTemplateOutputAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """Outputs are changed on their template's page, where the template's checks (at most 100%) run."""

    list_display = ('template', 'kind', 'product', 'name', 'expected_percent', 'keeps_days')
    list_select_related = ('template', 'product')


@admin.register(YieldTemplate)
class YieldTemplateAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Butchery yield templates (G20); outputs total at most 100%, the rest is the expected loss."""

    list_display = ('name', 'input_product', 'is_active', 'expected_loss_percent')
    list_filter = ('is_active',)
    search_fields = ('name', 'input_product__name')
    list_select_related = ('input_product',)
    inlines = [YieldTemplateOutputInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('outputs')  # expected_loss_percent per row


class BreakdownLineInline(admin.TabularInline):
    model = BreakdownLine
    fields = ('kind', 'name', 'weight_kg', 'pieces', 'expiration_date', 'output_batch')
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Breakdown)
class BreakdownAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """Butchery breakdowns (G21); booked and voided only through the API (the stock moves with them)."""

    list_display = ('reference', 'template', 'location', 'input_kg', 'loss_kg', 'status', 'created_at')
    list_filter = ('status',)
    list_select_related = ('template', 'location')
    inlines = [BreakdownLineInline]


@admin.register(BreakdownLine)
class BreakdownLineAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """A breakdown line changed by hand would no longer match its movements."""


@admin.register(WriteOff)
class WriteOffAdmin(TenantAdminMixin, ViewOnlyAdmin):
    """Waste write-offs (G23); raised, approved and rejected through the API (the stock moves with them)."""

    list_display = ('reference', 'batch', 'reason', 'quantity', 'weight_kg', 'cost', 'status', 'created_at')
    list_filter = ('status', 'reason')
    list_select_related = ('batch__product',)

