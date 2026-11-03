# inventory/views.py

from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from django.db import IntegrityError, OperationalError, transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from common.tenancy import IsCompanyStaffOrReadOnly, SharedReferenceMixin, TenantScopedMixin

from . import breakdowns, requisitions, transfers
from .models import BarcodeRule, Location, ProductCategory, Product, Order, Supplier, StockBatch, StockMovement, Transfer, Requisition, YieldTemplate, Breakdown
from .projections import project_stock
from .serializers import (
    BarcodeRuleSerializer,
    BreakdownCreateSerializer,
    BreakdownSerializer,
    BreakdownVoidSerializer,
    LocationSerializer,
    OrderSerializer,
    ProductCategorySerializer,
    ProductSerializer,
    StockBatchSerializer,
    StockMovementSerializer,
    StockProjectionSerializer,
    SupplierSerializer,
    RequisitionFulfilSerializer,
    RequisitionSerializer,
    TransferReceiveSerializer,
    TransferSerializer,
    YieldTemplateSerializer,
)


class ProductCategoryViewSet(SharedReferenceMixin, viewsets.ModelViewSet):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer


class ProductViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer

    @extend_schema(
        parameters=[OpenApiParameter('days', int, description='How many days ahead to project (1-180, default 30).')],
        responses=StockProjectionSerializer,
    )
    @action(detail=True, methods=['get'], url_path='stock-projection')
    def stock_projection(self, request, pk=None):
        """Projected stock by day, from recent sales and the demand calendar, with the expected stock-out date."""
        try:
            days = int(request.query_params.get('days', 30))
        except ValueError:
            days = 0
        if not 1 <= days <= 180:
            return Response({'days': ['Must be a whole number between 1 and 180.']}, status=400)
        return Response(StockProjectionSerializer(project_stock(self.get_object(), days)).data)


class OrderViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer


class SupplierViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('location', int, description='Only batches at this location.'),
    OpenApiParameter('product', int, description='Only batches of this product.'),
]))
class StockBatchViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = StockBatch.objects.all()
    serializer_class = StockBatchSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        for name in ('location', 'product'):
            value = self.request.query_params.get(name, '')
            if value.isascii() and value.isdigit():  # '²'.isdigit() is True but not an id
                queryset = queryset.filter(**{f'{name}_id': value})
        return queryset

    def perform_destroy(self, instance):
        if instance.location.is_virtual:
            raise ValidationError({'detail': 'Stock at a virtual location (e.g. in transit) is moved only by its transfer.'})
        instance.delete()


class StockMovementViewSet(
    TenantScopedMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Append-only stock ledger: creating a movement updates the batch and product stock."""

    queryset = StockMovement.objects.all()
    serializer_class = StockMovementSerializer


class CompanyMemberReadsStaffWrites(IsCompanyStaffOrReadOnly):
    """Members of a company read, its staff change; a user with no company (even a superuser) gets 403."""

    def has_permission(self, request, view):
        return bool(getattr(request.user, 'company_id', None)) and super().has_permission(request, view)


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('type', str, description='Only this type, e.g. warehouse.'),
    OpenApiParameter('branch', int, description='Only locations in this branch.'),
    OpenApiParameter('sellable', bool, description='true: only sellable locations; false: only the others.'),
]))
class LocationViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """
    Stock locations (any company member may read; only staff change them): shop floors, butchery, warehouses, back stores, vans, plus the virtual
    supplier/customer/waste/adjustment/in-transit locations every company has. Retire one with ``is_active``.
    """

    queryset = Location.objects.select_related('branch')
    serializer_class = LocationSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if params.get('type'):
            queryset = queryset.filter(type=params['type'])
        if params.get('branch', '').isdigit():
            queryset = queryset.filter(branch_id=params['branch'])
        if params.get('sellable') in ('true', 'false'):
            queryset = queryset.filter(sellable=params['sellable'] == 'true')
        return queryset

    def perform_destroy(self, instance):
        if instance.is_virtual:
            raise ValidationError({'detail': 'Virtual locations are managed by the system and cannot be removed.'})
        instance.delete()


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('status', str, description='Only transfers in this status (draft, dispatched, received, cancelled).'),
]))
class TransferViewSet(
    TenantScopedMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    """
    Stock transfers between the company's own locations: draft → dispatched (stock goes to IN-TRANSIT) → received
    (what arrived goes to the destination, any shortfall to ADJUSTMENT with a reason), or draft → cancelled.
    Transfers are never deleted.
    """

    queryset = Transfer.objects.prefetch_related('lines')
    serializer_class = TransferSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == 'list' and self.request.query_params.get('status'):
            queryset = queryset.filter(status=self.request.query_params['status'])
        return queryset

    def _done(self, step, *args):
        try:
            transfer = step(self.get_object(), self.request.user, *args)
        except transfers.TransferError as error:
            raise ValidationError(error.detail)
        except OperationalError as error:  # Postgres broke a deadlock with another stock change: nothing was saved
            if 'deadlock' not in str(error).lower():
                raise
            return Response({'detail': 'Stock was changing at the same time; nothing was saved. Try again.'},
                            status=status.HTTP_409_CONFLICT)
        return Response(TransferSerializer(self.get_queryset().get(pk=transfer.pk), context=self.get_serializer_context()).data)

    @extend_schema(request=None, responses=TransferSerializer)
    @action(detail=True, methods=['post'], url_path='dispatch', url_name='dispatch')
    def dispatch_transfer(self, request, pk=None):
        """Send the stock: each line is taken from the source (earliest expiry first) to IN-TRANSIT."""
        return self._done(transfers.dispatch)

    @extend_schema(request=TransferReceiveSerializer, responses=TransferSerializer)
    @action(detail=True, methods=['post'])
    def receive(self, request, pk=None):
        """Book what arrived, once, for every line; a difference needs a reason and goes to ADJUSTMENT."""
        serializer = TransferReceiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._done(transfers.receive, serializer.validated_data['lines'])

    @extend_schema(request=None, responses=TransferSerializer)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a draft. A dispatched transfer is closed by receiving it (0 if nothing arrived)."""
        return self._done(transfers.cancel)


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('status', str, description='Only requisitions in this status (open, partly_fulfilled, fulfilled, '
                                                'cancelled).'),
]))
class RequisitionViewSet(
    TenantScopedMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Store requisitions to the warehouse. Raise one for a store location; ``fulfil`` turns (part of) it into a draft
    transfer, ``cancel`` closes what is still open. Not edited or deleted.
    """

    queryset = Requisition.objects.prefetch_related('lines')
    serializer_class = RequisitionSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == 'list' and self.request.query_params.get('status'):
            queryset = queryset.filter(status=self.request.query_params['status'])
        return queryset

    @extend_schema(request=RequisitionFulfilSerializer, responses={201: TransferSerializer})
    @action(detail=True, methods=['post'])
    def fulfil(self, request, pk=None):
        """Make a draft transfer from ``source_location`` for the given amounts (each at most what is open)."""
        requisition = self.get_object()
        serializer = RequisitionFulfilSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        quantities = {}
        for line in serializer.validated_data['lines']:
            if line['line'] in quantities:
                raise ValidationError({'lines': ['Give each line once.']})
            quantities[line['line']] = line['quantity']
        try:
            transfer = requisitions.fulfil(requisition, request.user,
                                           source=serializer.validated_data['source_location'], quantities=quantities)
        except requisitions.RequisitionError as error:
            raise ValidationError(error.detail)
        return Response(TransferSerializer(Transfer.objects.prefetch_related('lines').get(pk=transfer.pk),
                                           context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=RequisitionSerializer)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Close what is still open; transfers already made stay."""
        try:
            requisition = requisitions.cancel(self.get_object(), request.user)
        except requisitions.RequisitionError as error:
            raise ValidationError(error.detail)
        return Response(RequisitionSerializer(self.get_queryset().get(pk=requisition.pk),
                                              context=self.get_serializer_context()).data)


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('active', bool, description='true: only the rules the till uses.'),
]))
class BarcodeRuleViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """
    Scale-label layouts (variable-measure EAN-13, prefix 2x) for this company. Company members and the till read
    them (``?active=true`` is what the till loads); only staff change them.
    """

    queryset = BarcodeRule.objects.all()
    serializer_class = BarcodeRuleSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def perform_create(self, serializer):
        self._save_once(super().perform_create, serializer)

    def perform_update(self, serializer):
        self._save_once(super().perform_update, serializer)

    def _save_once(self, save, serializer):
        try:  # two saves of one prefix at once: the loser hits the unique constraint
            with transaction.atomic():
                save(serializer)
        except IntegrityError:
            raise ValidationError({'prefix': 'Another rule already uses this prefix.'}) from None

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('active') == 'true':
            queryset = queryset.filter(is_active=True)
        return queryset


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('active', bool, description='true: only templates in use.'),
]))
class YieldTemplateViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """
    Butchery yield templates (G20): an input product and the outputs and waste it is expected to break down into.
    Company members and the stock role read them; only staff change them.
    """

    queryset = YieldTemplate.objects.select_related('input_product').prefetch_related('outputs__product')
    serializer_class = YieldTemplateSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def perform_create(self, serializer):
        self._save_once(super().perform_create, serializer)

    def perform_update(self, serializer):
        self._save_once(super().perform_update, serializer)

    def _save_once(self, save, serializer):
        try:  # two saves of one name at once: the loser hits the unique constraint
            with transaction.atomic():
                save(serializer)
        except IntegrityError:
            raise ValidationError({'name': 'Another template already has this name.'}) from None

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('active') == 'true':
            queryset = queryset.filter(is_active=True)
        return queryset


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('status', str, enum=[Breakdown.COMPLETED, Breakdown.VOIDED]),
]))
class BreakdownViewSet(
    TenantScopedMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Butchery breakdowns (G21): an input batch at a butchery location cut into a yield template's products (new
    batches there), waste (to WASTE) and loss (to ADJUSTMENT). Recorded in one step, reversed by ``void``; never
    edited or deleted. Staff and the stock role.
    """

    queryset = Breakdown.objects.select_related('template', 'input_batch__product').prefetch_related(
        'lines__template_output', 'lines__breakdown')
    serializer_class = BreakdownSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == 'list' and self.request.query_params.get('status'):
            queryset = queryset.filter(status=self.request.query_params['status'])
        return queryset

    def _response(self, breakdown, code):
        return Response(BreakdownSerializer(self.get_queryset().get(pk=breakdown.pk),
                                            context=self.get_serializer_context()).data, status=code)

    @extend_schema(request=BreakdownCreateSerializer, responses={201: BreakdownSerializer, 200: BreakdownSerializer})
    def create(self, request, *args, **kwargs):
        serializer = BreakdownCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if request.user.company is None:
            raise ValidationError({'detail': ['Your account is not linked to a company.']})
        try:
            breakdown, created = breakdowns.record_breakdown(
                company=request.user.company, user=request.user, template=data['template'],
                location=data['location'], input_batch=data['input_batch'], input_kg=data.get('input_kg'),
                input_pieces=data.get('input_pieces'), loss_reason=data['loss_reason'], client_ref=data['client_ref'],
                lines=[(line['template_output'], line['weight_kg'], line['pieces'], line['expiration_date'])
                       for line in data['lines']],
            )
        except breakdowns.BreakdownError as error:
            raise ValidationError(error.detail)
        return self._response(breakdown, status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @extend_schema(request=BreakdownVoidSerializer, responses=BreakdownSerializer)
    @action(detail=True, methods=['post'])
    def void(self, request, pk=None):
        """Reverse every movement (refused once an output has been sold or moved)."""
        breakdown = self.get_object()
        serializer = BreakdownVoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            breakdown = breakdowns.void_breakdown(breakdown, user=request.user,
                                                  reason=serializer.validated_data['reason'])
        except breakdowns.BreakdownError as error:
            raise ValidationError(error.detail)
        return self._response(breakdown, status.HTTP_200_OK)
