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

from rest_framework import permissions, serializers

TENANT_LOOKUPS = {
    'tenants.Branch': 'company',
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
    'pricing.Subscription': 'user__company',
    'pricing.SupplierInvoice': 'company',
    'pricing.InvoiceLineItem': 'invoice__company',
    'pricing.PriceChangeLog': 'product__company',
    'sync.SyncTask': 'company',
    'sync.ShopifyConnection': 'company',
    'common.DemandAlert': 'company',
    'common.StockAlert': 'product__company',
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
        return scope_queryset(super().get_queryset(), self.request.user)

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
    """

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
