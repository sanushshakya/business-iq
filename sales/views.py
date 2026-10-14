# sales/views.py

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from common.tenancy import TenantScopedMixin

from .models import Sale, TillSession
from .serializers import (
    CloseSessionSerializer, CreateSaleSerializer, OpenSessionSerializer, SaleSerializer,
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

    queryset = Sale.objects.select_related('session__till__branch', 'cashier').prefetch_related('items')
    serializer_class = SaleSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
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
            lines=[(line['product'], line['quantity']) for line in data['items']],
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
