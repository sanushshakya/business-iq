# sales/views.py

from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenancy import HasCompany, TenantScopedMixin
from django.utils import timezone
from tenants.views import IsCompanyStaff

from .catalogue import catalogue, read_cursor
from .offline_sync import sync_offline_sales
from .models import Sale, SaleException, TillSession
from .serializers import (
    CatalogueSerializer, CloseSessionSerializer, CreateSaleSerializer, OfflineSaleBatchSerializer,
    OfflineSaleResultsSerializer, SaleExceptionSerializer, OpenSessionSerializer, SaleSerializer,
    TillSessionSerializer, VoidSaleSerializer,
)
from .services import close_session, open_session, record_sale, void_sale


def _is_manager(user):
    return user.is_staff or user.is_superuser


class TillSessionViewSet(
    TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Till shifts. Open one to start selling, close it to cash up."""

    queryset = TillSession.objects.select_related('till__branch', 'opened_by', 'closed_by')
    serializer_class = TillSessionSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_till_user:
            queryset = queryset.filter(opened_by=self.request.user)
        params = self.request.query_params
        if params.get('till', '').isdigit():
            queryset = queryset.filter(till_id=params['till'])
        state = params.get('status')
        if state == 'open':
            queryset = queryset.filter(closed_at__isnull=True)
        elif state == 'closed':
            queryset = queryset.filter(closed_at__isnull=False)
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter('till', int, description='Only this till.'),
            OpenApiParameter('status', str, enum=['open', 'closed'], description='Only open or closed sessions.'),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=OpenSessionSerializer, responses={201: TillSessionSerializer})
    def create(self, request):
        """Open a session on a till with a starting cash float. A till has one open session at a time."""
        serializer = OpenSessionSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        session = open_session(
            till=serializer.validated_data['till'], user=request.user,
            opening_float=serializer.validated_data['opening_float'],
        )
        return Response(TillSessionSerializer(session).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=CloseSessionSerializer, responses=TillSessionSerializer)
    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        """Cash up: record the counted cash. Only the person who opened the session, or staff, can close it."""
        session = self.get_object()
        if session.opened_by_id != request.user.pk and not _is_manager(request.user):
            raise PermissionDenied('Only the person who opened this session, or staff, can close it.')
        serializer = CloseSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = close_session(session=session, user=request.user, **serializer.validated_data)
        return Response(TillSessionSerializer(session).data)


class SaleViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Sales rung up at tills. Creating one takes stock; voiding one puts it back."""

    queryset = Sale.objects.select_related('session__till__branch', 'cashier').prefetch_related('items', 'promotions')
    serializer_class = SaleSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_till_user:
            queryset = queryset.filter(cashier=self.request.user)
        params = self.request.query_params
        for key, field in (('session', 'session_id'), ('till', 'session__till_id')):
            if params.get(key, '').isdigit():
                queryset = queryset.filter(**{field: params[key]})
        if params.get('status') in (Sale.COMPLETED, Sale.VOIDED):
            queryset = queryset.filter(status=params['status'])
        if params.get('payment_method') in (Sale.CASH, Sale.CARD):
            queryset = queryset.filter(payment_method=params['payment_method'])
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter('session', int, description='Only this session.'),
            OpenApiParameter('till', int, description='Only this till.'),
            OpenApiParameter('status', str, enum=['completed', 'voided']),
            OpenApiParameter('payment_method', str, enum=['cash', 'card']),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=CreateSaleSerializer, responses={201: SaleSerializer})
    def create(self, request):
        """Ring up a sale. Prices come from the products; stock is taken oldest expiry first.

        Send the same ``client_ref`` (a UUID) when retrying: the original sale comes back with 200, not a second charge.
        """
        serializer = CreateSaleSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        sale, created = record_sale(
            session=data['session'], cashier=request.user, payment_method=data['payment_method'],
            amount_tendered=data.get('amount_tendered'), client_ref=data['client_ref'],
            loyalty_barcode=data['loyalty_barcode'], card_auth_code=data['card_auth_code'], card_last4=data['card_last4'],
            lines=[(line['product'], line['quantity'], line.get('weight_kg'), line.get('label'))
                   for line in data['items']],
        )
        return Response(
            SaleSerializer(self.get_queryset().get(pk=sale.pk)).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(request=VoidSaleSerializer, responses=SaleSerializer)
    @action(detail=True, methods=['post'])
    def void(self, request, pk=None):
        """Cancel a sale and return its stock. Staff can void any sale; a cashier only their own, while the session is open."""
        sale = self.get_object()
        if sale.cashier_id != request.user.pk and not _is_manager(request.user):
            raise PermissionDenied('Only the cashier who made this sale, or staff, can void it.')
        serializer = VoidSaleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        void_sale(sale=sale, user=request.user, reason=serializer.validated_data['reason'])
        return Response(SaleSerializer(self.get_queryset().get(pk=sale.pk)).data)


class CatalogueView(APIView):
    """
    The till catalogue (G26): products, prices, VAT, barcode rules, tills and till settings, which the till keeps in
    its browser to keep finding and pricing products while offline. Pass the last ``cursor`` as ``since`` to get only
    what changed.
    """

    permission_classes = [IsAuthenticated, HasCompany]

    @extend_schema(parameters=[OpenApiParameter('since', str, description='The cursor of the last refresh.')],
                   responses=CatalogueSerializer)
    def get(self, request):
        since = read_cursor(request.query_params.get('since'))
        return Response(CatalogueSerializer(catalogue(request.user, since)).data)


class OfflineSalesView(APIView):
    """
    Sales a till made while offline (G27), sent when it reconnects (G28): recorded oldest first, each on its own, at
    what the till charged. A sale is never refused for differing from today's view (stock, price, a closed session...):
    those become exceptions for a manager. Sending a sale again changes nothing (``duplicate``); ``error`` means it
    could not be recorded yet and stays on the till.
    """

    permission_classes = [IsAuthenticated, HasCompany]

    @extend_schema(request=OfflineSaleBatchSerializer, responses=OfflineSaleResultsSerializer)
    def post(self, request):
        serializer = OfflineSaleBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        results = sync_offline_sales(request.user, serializer.validated_data['sales'])
        return Response(OfflineSaleResultsSerializer({'results': results}).data)


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('resolved', bool, description='true: handled ones; default: the open ones.'),
    OpenApiParameter('kind', str, enum=[kind for kind, _ in SaleException.KINDS]),
]))
class SaleExceptionViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                           viewsets.GenericViewSet):
    """
    Differences offline sales showed when they were synced (G28): price, stock, labels, a closed session...
    The sales stand (they were paid); a manager looks at each and marks it handled. Staff only.
    """

    queryset = SaleException.objects.select_related('sale', 'product', 'resolved_by')
    serializer_class = SaleExceptionSerializer
    permission_classes = [IsCompanyStaff]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != 'list':
            return queryset
        params = self.request.query_params
        queryset = queryset.filter(resolved_at__isnull=params.get('resolved') != 'true')
        if params.get('kind'):
            queryset = queryset.filter(kind=params['kind'])
        return queryset

    @extend_schema(request=None, responses=SaleExceptionSerializer)
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Mark it handled (who and when are recorded)."""
        exception = self.get_object()
        # Only an open one changes: two managers pressing at once can't overwrite each other.
        changed = SaleException.objects.filter(pk=exception.pk, resolved_at__isnull=True).update(
            resolved_at=timezone.now(), resolved_by=request.user)
        if not changed:
            raise ValidationError({'detail': ['This one is already handled.']})
        exception.refresh_from_db()
        return Response(self.get_serializer(exception).data)
