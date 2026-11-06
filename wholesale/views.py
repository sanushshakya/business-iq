from datetime import date

from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenancy import TenantScopedMixin
from inventory.views import CompanyMemberReadsStaffWrites
from vat.services import uk_date

from . import orders
from .models import CustomerGroup, PriceList, PriceListEntry, SalesOrder, TradeCustomer
from .pricing import resolve_prices
from .serializers import (
    CancelOrderSerializer,
    CustomerGroupSerializer,
    PriceListSerializer,
    ResolveRequestSerializer,
    ResolveResponseSerializer,
    SalesOrderSerializer,
    TradeCustomerSerializer,
)

STATUS_VALUES = {value for value, _label in TradeCustomer.STATUSES}


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('status', str, enum=sorted(STATUS_VALUES), description='Only customers with this status.'),
    OpenApiParameter('search', str, description='Part of the account code, legal or trading name.'),
]))
class TradeCustomerViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                           mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """
    Trade customers (G38): businesses that buy on account. Company members read; staff create and change them.
    There is no delete: set ``status`` to ``closed`` (orders and invoices will point at customers).
    """

    queryset = TradeCustomer.objects.prefetch_related('addresses', 'contacts')
    serializer_class = TradeCustomerSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def get_queryset(self):
        queryset = super().get_queryset()
        status = self.request.query_params.get('status')
        if status is not None:
            if status not in STATUS_VALUES:
                raise ValidationError({'status': f'One of {", ".join(sorted(STATUS_VALUES))}.'})
            queryset = queryset.filter(status=status)
        search = (self.request.query_params.get('search') or '').strip()
        if search:
            queryset = queryset.filter(
                Q(account_code__icontains=search) | Q(legal_name__icontains=search) | Q(trading_name__icontains=search)
            )
        return queryset

    def perform_create(self, serializer):
        self._save_once(super().perform_create, serializer)

    def perform_update(self, serializer):
        self._save_once(super().perform_update, serializer, lock=serializer.instance.pk)

    def _save_once(self, save, serializer, lock=None):
        try:
            with transaction.atomic():
                if lock is not None:  # two edits replacing addresses at once take turns
                    TradeCustomer.objects.select_for_update().filter(pk=lock).first()
                save(serializer)
        except IntegrityError as error:
            # Two saves of one code at once: the loser hits the unique constraint (named on Postgres; SQLite
            # names the columns instead).
            if any(mark in str(error) for mark in ('unique_trade_customer_code', 'tradecustomer.account_code')):
                raise ValidationError({'account_code': 'Another customer already has this account code.'}) from None
            raise ValidationError({'detail': 'This customer was changed at the same time; try again.'}) from None


def _unique_name_error(error, constraint, table_column, message):
    """A lost race on a unique name: the constraint is named on Postgres; SQLite names the columns instead."""
    if any(mark in str(error) for mark in (constraint, table_column)):
        return ValidationError({'name': message})
    return ValidationError({'detail': 'This was changed at the same time; try again.'})


class CustomerGroupViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """Customer groups (G39): trade customers priced alike. Members read; staff change them. Deleting a group
    leaves its customers with no group (and takes it off its price lists)."""

    queryset = CustomerGroup.objects.all()
    serializer_class = CustomerGroupSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                super().perform_create(serializer)
        except IntegrityError as error:
            raise _unique_name_error(error, 'unique_customer_group_name', 'customergroup.name',
                                     'Another group already has this name.') from None

    def perform_update(self, serializer):
        try:
            with transaction.atomic():
                super().perform_update(serializer)
        except IntegrityError as error:
            raise _unique_name_error(error, 'unique_customer_group_name', 'customergroup.name',
                                     'Another group already has this name.') from None


def _id_param(params, name):
    raw = params.get(name)
    if raw is None:
        return None
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 18):
        raise ValidationError({name: 'An id.'})
    return int(raw)


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('active', bool, description='true: only active lists; false: only inactive ones.'),
    OpenApiParameter('customer', int, description='Only lists assigned to this trade customer.'),
    OpenApiParameter('group', int, description='Only lists assigned to this customer group.'),
    OpenApiParameter('product', int, description='Only lists that price this product.'),
]))
class PriceListViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """
    Price lists (G39): trade prices ex VAT with quantity breaks and dates, for customers and groups. Members read;
    staff change them. ``entries`` is nested: sending it replaces them all. The highest ``priority`` wins where lists
    overlap at a level, then the newest.
    """

    queryset = PriceList.objects.prefetch_related(
        'customers', 'groups', Prefetch('entries', queryset=PriceListEntry.objects.select_related('product')),
    )
    serializer_class = PriceListSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        active = params.get('active')
        if active is not None:
            if active not in ('true', 'false'):
                raise ValidationError({'active': 'true or false.'})
            queryset = queryset.filter(is_active=active == 'true')
        for name, lookup in (('customer', 'customers'), ('group', 'groups'), ('product', 'entries__product')):
            value = _id_param(params, name)
            if value is not None:
                queryset = queryset.filter(**{lookup: value})
        return queryset.distinct()

    def perform_create(self, serializer):
        self._save_once(super().perform_create, serializer)

    def perform_update(self, serializer):
        self._save_once(super().perform_update, serializer, lock=serializer.instance.pk)

    def _save_once(self, save, serializer, lock=None):
        try:
            with transaction.atomic():
                if lock is not None:  # two edits replacing entries at once take turns
                    PriceList.objects.select_for_update().filter(pk=lock).first()
                save(serializer)
        except IntegrityError as error:
            raise _unique_name_error(error, 'unique_price_list_name', 'pricelist.name',
                                     'Another price list already has this name.') from None


class IsCompanyMember(BasePermission):
    """Anyone in a company (operators are stopped earlier, by ``role_access``)."""

    def has_permission(self, request, view):
        return bool(getattr(request.user, 'company_id', None))


class PriceResolveView(APIView):
    """
    What a trade customer pays per unit ex VAT (per kg for weighed products) for each line, on a UK date (today by
    default): the customer's lists, then its group's, then the product's wholesale price (G39). A line with none of
    these comes back with ``error`` "no_price". At most 500 lines. Company members may ask.
    """

    permission_classes = [IsCompanyMember]

    @extend_schema(request=ResolveRequestSerializer, responses=ResolveResponseSerializer)
    def post(self, request):
        serializer = ResolveRequestSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        customer = serializer.validated_data['customer']
        on = serializer.validated_data.get('on') or uk_date()
        lines = [(line['product'], line['quantity']) for line in serializer.validated_data['lines']]
        rows = []
        for (product, quantity), price in zip(lines, resolve_prices(customer, lines, on=on), strict=True):
            row = {'product': product.pk, 'quantity': quantity}
            if price is None:
                row['error'] = 'no_price'
            else:
                row.update(unit_price=price.unit_price, source=price.source,
                           price_list=price.price_list.pk if price.price_list else None,
                           price_list_name=price.price_list.name if price.price_list else None,
                           min_quantity=price.min_quantity)
            rows.append(row)
        return Response(ResolveResponseSerializer({'customer': customer.pk, 'on': on, 'lines': rows}).data)


ORDER_STATUSES = {value for value, _label in SalesOrder.STATUSES}


def _date_param(params, name):
    raw = params.get(name)
    if raw is None:
        return None
    try:
        if len(raw) != 10:  # fromisoformat also takes forms such as 20261113
            raise ValueError(raw)
        return date.fromisoformat(raw)
    except ValueError:
        raise ValidationError({name: 'A date as YYYY-MM-DD.'}) from None


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('status', str, enum=sorted(ORDER_STATUSES), description='Only orders in this status.'),
    OpenApiParameter('customer', int, description='Only this trade customer\'s orders.'),
    OpenApiParameter('delivery_date', OpenApiTypes.DATE, description='Only orders delivered on this day.'),
    OpenApiParameter('from', OpenApiTypes.DATE, description='Delivered on or after this day.'),
    OpenApiParameter('to', OpenApiTypes.DATE, description='Delivered on or before this day.'),
]))
class SalesOrderViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                        mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """
    Trade sales orders (G40), entered by staff: draft -> confirmed -> picking -> picked -> dispatched -> delivered
    -> invoiced, or cancelled up to picking. Only a draft can be edited; ``confirm`` fixes the prices (from the
    price lists valid on the delivery date) and ``cancel`` needs a reason. Picking onwards come with G41-G44.
    Members read; staff change. There is no delete: cancel instead.
    """

    queryset = SalesOrder.objects.select_related('customer').prefetch_related('lines__product')
    serializer_class = SalesOrderSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        status = params.get('status')
        if status is not None:
            if status not in ORDER_STATUSES:
                raise ValidationError({'status': f'One of {", ".join(sorted(ORDER_STATUSES))}.'})
            queryset = queryset.filter(status=status)
        customer = _id_param(params, 'customer')
        if customer is not None:
            queryset = queryset.filter(customer_id=customer)
        for name, lookup in (('delivery_date', 'delivery_date'), ('from', 'delivery_date__gte'),
                             ('to', 'delivery_date__lte')):
            value = _date_param(params, name)
            if value is not None:
                queryset = queryset.filter(**{lookup: value})
        return queryset

    def perform_update(self, serializer):
        with transaction.atomic():  # the draft check and the edit happen under the order's lock
            super().perform_update(serializer)

    def _moved(self, step, *args):
        try:
            order = step(self.get_object(), self.request.user, *args)
        except orders.OrderStateError as error:
            raise ValidationError({'detail': error.detail}) from None
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data)

    @extend_schema(request=None, responses=SalesOrderSerializer)
    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """Fix each line's price (lists valid on the delivery date, then the wholesale price) and confirm. Refused
        when a line has no trade price or the customer is on hold or closed."""
        return self._moved(orders.confirm)

    @extend_schema(request=CancelOrderSerializer, responses=SalesOrderSerializer)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a draft, confirmed or picking order, with a reason."""
        serializer = CancelOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._moved(orders.cancel, serializer.validated_data['reason'])
