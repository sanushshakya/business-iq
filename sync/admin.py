from django import forms
from django.contrib import admin, messages
from django.db import transaction

from common.tenancy import TenantAdminMixin
from inventory.models import Product

from .catalogue import exclude_item, link_item, summary
from .models import (
    CatalogueImport, CatalogueImportItem, InboundEvent, OutboundEvent, PricePushChange, ShopifyConnection,
    StockReconciliation, StockSyncState, StorefrontConnection, StorefrontExclusion, SyncTask,
)


@admin.register(SyncTask)
class SyncTaskAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('task_id', 'source_system', 'target_system', 'status', 'start_time', 'end_time')
    list_filter = ('status',)
    search_fields = ('task_id',)
    readonly_fields = ('task_id', 'status', 'start_time', 'end_time')


@admin.register(ShopifyConnection)
class ShopifyConnectionAdmin(TenantAdminMixin, admin.ModelAdmin):
    """The access token can be entered when connecting a store but is never displayed afterwards."""

    list_display = ('shop_domain', 'company', 'created_at')
    search_fields = ('shop_domain',)

    def get_exclude(self, request, obj=None):
        return ('access_token',) if obj else ()


@admin.register(StorefrontConnection)
class StorefrontConnectionAdmin(TenantAdminMixin, admin.ModelAdmin):
    """The webhook secret can be entered when connecting the storefront but is never displayed afterwards."""

    list_display = ('name', 'company', 'base_url', 'is_active', 'push_prices', 'created_at')

    def get_exclude(self, request, obj=None):
        return ('webhook_secret',) if obj else ()

    def save_model(self, request, obj, form, change):
        obj.changed_by = request.user  # recorded in PricePushChange if push_prices is switched
        super().save_model(request, obj, form, change)
        if obj.push_prices and 'push_prices' in form.changed_data:
            # Prices only travel with a product's snapshot: queue them all so every price goes out on the next sweep.
            from .stock import mark_all

            company_id = obj.company_id
            transaction.on_commit(lambda: mark_all(company_id))


@admin.register(InboundEvent)
class InboundEventAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Webhooks received from the storefront. Read-only and never deleted: each row is what stops a redelivered
    order from being applied to stock twice."""

    list_display = ('event_type', 'event_id', 'status', 'connection', 'received_at')
    list_filter = ('status', 'event_type')
    search_fields = ('event_id',)
    readonly_fields = ('connection', 'event_id', 'event_type', 'status', 'detail', 'payload', 'received_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OutboundEvent)
class OutboundEventAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Events sent to the storefront (loyalty awards and reversals). Read-only: the sweep owns their state."""

    list_display = ('event_type', 'event_id', 'status', 'attempts', 'next_attempt_at', 'connection', 'created_at')
    list_filter = ('status', 'event_type')
    search_fields = ('event_id',)
    readonly_fields = (
        'connection', 'event_id', 'event_type', 'status', 'attempts', 'next_attempt_at', 'last_error', 'payload',
        'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(TenantAdminMixin, admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PricePushChange)
class PricePushChangeAdmin(ReadOnlyAdmin):
    """Who switched price pushing on or off, and when (see docs/runbooks/price-push.md)."""

    list_display = ('connection', 'turned_on', 'changed_by', 'changed_at')
    list_filter = ('turned_on',)
    list_select_related = ('connection', 'changed_by')


@admin.register(StockSyncState)
class StockSyncStateAdmin(ReadOnlyAdmin):
    """Online stock last sent to the storefront per product; ``pending`` ones go out within ~15 s."""

    list_display = ('product', 'pending', 'last_sent_available', 'version', 'changed_at')
    list_filter = ('pending',)
    search_fields = ('product__name', 'product__external_id')


@admin.register(StockReconciliation)
class StockReconciliationAdmin(ReadOnlyAdmin):
    """Nightly stock comparison with the storefront. Mismatches are fixed by the resend that follows each run."""

    list_display = ('ran_at', 'connection', 'checked', 'mismatch_count', 'error')
    readonly_fields = ('connection', 'ran_at', 'checked', 'mismatches', 'error')

    @admin.display(description='Mismatches')
    def mismatch_count(self, obj):
        return len(obj.mismatches)


class CatalogueImportItemInline(admin.TabularInline):
    model = CatalogueImportItem
    fields = ('name', 'outcome', 'detail', 'product', 'm18_price', 'iq_price', 'resolved_by')
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CatalogueImport)
class CatalogueImportAdmin(ReadOnlyAdmin):
    """Runs of ``import_storefront_products``. Act on the items that need attention from the item list."""

    list_display = ('ran_at', 'dry_run', 'source', 'run_by', 'result')
    list_filter = ('dry_run',)
    readonly_fields = ('ran_at', 'run_by', 'dry_run', 'source', 'result')
    exclude = ('counts',)
    inlines = [CatalogueImportItemInline]

    @admin.display(description='Result')
    def result(self, run):
        return summary(run)


class LinkItemForm(forms.ModelForm):
    """The one thing staff can change on an import item: which unlinked IQ product it is."""

    link_to = forms.ModelChoiceField(
        queryset=Product.objects.none(), required=False, label='Link to IQ product',
        help_text="One of the company's products not yet linked to the shop.",
    )

    class Meta:
        model = CatalogueImportItem
        fields = []

    def clean_link_to(self):
        product = self.cleaned_data['link_to']
        item = self.instance
        if product and Product.objects.filter(company=item.run.company, external_id=item.m18_product_id).exists():
            raise forms.ValidationError('This m18 product is already linked to an IQ product.')
        return product


@admin.register(CatalogueImportItem)
class CatalogueImportItemAdmin(TenantAdminMixin, admin.ModelAdmin):
    """
    One m18 product in an import. For ambiguous or skipped ones: pick the IQ product it is ("Link to IQ product"),
    or select them in the list and "Mark not sold online".
    """

    form = LinkItemForm
    list_display = ('name', 'outcome', 'product', 'm18_price', 'iq_price', 'resolved_by', 'run')
    list_filter = ('outcome', ('run', admin.RelatedOnlyFieldListFilter))  # only this company's runs
    search_fields = ('name', 'm18_product_id', 'barcode')
    list_select_related = ('product', 'run', 'resolved_by')
    readonly_fields = ('run', 'm18_product_id', 'name', 'barcode', 'outcome', 'detail', 'product', 'candidates',
                       'm18_price', 'iq_price', 'resolved_by', 'resolved_at')
    actions = ['mark_not_sold_online']

    def can_link(self, item):
        return item is not None and item.outcome in CatalogueImportItem.NEEDS_ATTENTION and item.resolved_at is None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_fields(self, request, obj=None):
        return (['link_to'] if self.can_link(obj) else []) + list(self.readonly_fields)

    def get_form(self, request, obj=None, change=False, **kwargs):
        kwargs['fields'] = []
        form = super().get_form(request, obj, change, **kwargs)
        if self.can_link(obj):
            form.base_fields['link_to'].queryset = Product.objects.filter(
                company=obj.run.company, external_id=''
            ).order_by('name')
        else:
            form.base_fields.pop('link_to', None)
        return form

    def save_model(self, request, obj, form, change):
        product = form.cleaned_data.get('link_to')
        if product and self.can_link(obj):
            link_item(obj, product, request.user)

    @admin.action(description='Mark not sold online')
    def mark_not_sold_online(self, request, queryset):
        items = queryset.filter(outcome__in=CatalogueImportItem.NEEDS_ATTENTION).select_related('run')
        for item in items:
            exclude_item(item, request.user)
        skipped = queryset.count() - len(items)
        note = f' {skipped} already linked or excluded, left as they were.' if skipped else ''
        self.message_user(request, f'{len(items)} product(s) marked not sold online.{note}', messages.SUCCESS)


@admin.register(StorefrontExclusion)
class StorefrontExclusionAdmin(TenantAdminMixin, admin.ModelAdmin):
    """m18 products the import skips. Delete one to have the next import link or create it again."""

    list_display = ('name', 'm18_product_id', 'reason', 'excluded_by', 'excluded_at')
    search_fields = ('name', 'm18_product_id')
    readonly_fields = ('m18_product_id', 'name', 'excluded_by', 'excluded_at')

    def has_add_permission(self, request):
        return False
