# common/tenancy.py

"""
Company-level data scoping.

Every tenant-owned model is listed in ``TENANT_LOOKUPS`` with the ORM path that leads to its
company. Viewsets that use ``TenantScopedMixin`` and serializers that extend
``TenantModelSerializer`` then guarantee that a user only sees, creates, edits or references rows
belonging to their own company. Superusers are not scoped.

Models that are not listed (ProductCategory, CulturalEvent, PricingPlan, the demand calendar) are
shared reference data: any signed-in user can read them, only staff can change them.
"""

from django.db.models import UniqueConstraint
from rest_framework import permissions, serializers

TENANT_LOOKUPS = {
    'tenants.Branch': 'company',
    'tenants.Till': 'branch__company',
    'tenants.CustomUser': 'company',
    'inventory.Product': 'company',
    'inventory.Supplier': 'company',
    'inventory.Order': 'product__company',
    'inventory.StockBatch': 'product__company',
    'inventory.StockMovement': 'batch__product__company',
    'demand.Demand': 'company',
    'demand.EventProductKeyword': 'product__company',
    'logistics.FreightAlert': 'company',
    'logistics.LogisticProvider': 'company',
    'logistics.Delivery': 'provider__company',
    'logistics.UserSupplier': 'company',
    'logistics.AlternativeSupplier': 'company',
    'pricing.Subscription': 'user__company',
    'pricing.SupplierInvoice': 'company',
    'pricing.InvoiceLineItem': 'invoice__company',
    'pricing.PriceChangeLog': 'product__company',
    'sync.SyncTask': 'company',
    'sync.ShopifyConnection': 'company',
    'common.DemandAlert': 'company',
    'common.StockAlert': 'product__company',
    'common.FreightRateCache': 'company',
    'common.Setting': 'company',
    'authentication.UserInvitation': 'company',
}


def scope_queryset(queryset, user):
    """Restrict ``queryset`` to rows the user's company owns (no-op for shared models)."""
    lookup = TENANT_LOOKUPS.get(queryset.model._meta.label)
    if lookup is None:
        return queryset
    if user is None or not user.is_authenticated:
        return queryset.none()
    if user.is_superuser:
        return queryset
    if user.company_id is None:
        return queryset.none()
    return queryset.filter(**{lookup: user.company_id})


class HasCompany(permissions.BasePermission):
    """Signed-in users who belong to a company (superusers are exempt)."""

    message = 'Your account is not linked to a company.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and (user.is_superuser or user.company_id))


class IsStaffOrReadOnly(permissions.BasePermission):
    """Signed-in users may read; only staff may write. For shared reference data."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return request.method in permissions.SAFE_METHODS or user.is_staff


def _stable_order(queryset):
    """Paginated lists need a deterministic order; fall back to primary key."""
    return queryset if queryset.ordered else queryset.order_by('pk')


class SharedReferenceMixin:
    """For shared reference data: readable by any signed-in user, writable by staff only."""

    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        return _stable_order(super().get_queryset())


class IsCompanyStaffOrReadOnly(permissions.BasePermission):
    """Any member of the company may read; only staff (or superusers) may change company-level data."""

    message = 'Only staff can change this.'

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and (user.is_superuser or user.company_id)):
            return False
        return request.method in permissions.SAFE_METHODS or user.is_staff


class TenantScopedMixin:
    """
    For DRF generic views / viewsets over a tenant-owned model.

    * limits the queryset to the user's company (other companies' rows are simply 404)
    * stamps ``company`` on create when the model has one
    """

    permission_classes = [permissions.IsAuthenticated, HasCompany]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return super().get_queryset().none()
        return _stable_order(scope_queryset(super().get_queryset(), self.request.user))

    def perform_create(self, serializer):
        user = self.request.user
        model = serializer.Meta.model
        if user.company_id and any(f.name == 'company' for f in model._meta.concrete_fields):
            serializer.save(company=user.company)
        else:
            serializer.save()


class TenantModelSerializer(serializers.ModelSerializer):
    """
    ModelSerializer that stops one company referencing another's data.

    * related-object fields only accept rows the user's company owns
    * ``company`` is read-only for users that belong to a company (it is set from the user)
    * unique constraints that include ``company`` are checked against the user's company, so a duplicate
      is a clean 400 (DRF skips them because ``company`` is read-only) and one company can never learn
      that another uses a name or number
    """

    def validate(self, attrs):
        attrs = super().validate(attrs)
        self._check_company_unique_constraints(attrs)
        return attrs

    def _company_id_for(self, attrs):
        if self.instance is not None:
            return getattr(self.instance, 'company_id', None)
        user = getattr(self.context.get('request'), 'user', None)
        if user is not None and getattr(user, 'company_id', None):
            return user.company_id
        company = attrs.get('company')
        return company.pk if company else None

    def _check_company_unique_constraints(self, attrs):
        model = self.Meta.model
        if not any(f.name == 'company' for f in model._meta.concrete_fields):
            return
        company_id = self._company_id_for(attrs)
        if company_id is None:
            return

        constraints = [c.fields for c in model._meta.constraints if isinstance(c, UniqueConstraint) and not c.condition]
        constraints += [list(group) for group in model._meta.unique_together]
        for fields in constraints:
            if 'company' not in fields:
                continue
            others = [f for f in fields if f != 'company']
            lookup = {}
            for name in others:
                if name in attrs:
                    lookup[name] = attrs[name]
                elif self.instance is not None:
                    lookup[name] = getattr(self.instance, name)
            if len(lookup) != len(others):
                continue  # a field is missing (a required-field error will be reported anyway)
            clash = model.objects.filter(company_id=company_id, **lookup)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError({others[0]: f'A record with this {others[0].replace("_", " ")} already exists.'})

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return fields

        for field in fields.values():
            related = field.child_relation if isinstance(field, serializers.ManyRelatedField) else field
            queryset = getattr(related, 'queryset', None)
            if isinstance(related, serializers.RelatedField) and queryset is not None:
                related.queryset = scope_queryset(queryset, user)

        company = fields.get('company')
        if company is not None and user.company_id:
            company.read_only = True
            company.required = False
        return fields


class TenantAdminMixin:
    """
    Django admin counterpart of ``TenantScopedMixin``: staff who belong to a company only see and
    edit that company's rows, can only pick that company's objects in foreign-key widgets, and
    don't see the ``company`` field (it is filled in on save). Superusers are unrestricted.
    """

    def _scoped(self, request):
        return not request.user.is_superuser and request.user.company_id is not None

    def get_queryset(self, request):
        return scope_queryset(super().get_queryset(request), request.user)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        queryset = kwargs.get('queryset', db_field.remote_field.model._default_manager.all())
        kwargs['queryset'] = scope_queryset(queryset, request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        queryset = kwargs.get('queryset', db_field.remote_field.model._default_manager.all())
        kwargs['queryset'] = scope_queryset(queryset, request.user)
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_exclude(self, request, obj=None):
        exclude = list(super().get_exclude(request, obj) or [])
        if self._scoped(request) and any(f.name == 'company' for f in self.model._meta.concrete_fields):
            exclude.append('company')
        return exclude

    def save_model(self, request, obj, form, change):
        has_company = any(f.name == 'company' for f in obj._meta.concrete_fields)
        if has_company and self._scoped(request) and obj.company_id is None:
            obj.company = request.user.company
        super().save_model(request, obj, form, change)
