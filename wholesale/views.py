from datetime import date

from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.db.models import Prefetch, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenancy import TenantScopedMixin
from inventory.models import StockReservation
from inventory.views import CompanyMemberReadsStaffWrites
from tenants.roles import DRIVER, STOCK
from vat.services import uk_date

from . import credit_notes, invoices, orders, picking, returns, runs
from .manifest_pdf import build_manifest_pdf
from .invoice_pdf import build_credit_note_pdf, build_invoice_pdf
from .pick_list import build_pick_list
from .models import (
    CreditNote,
    CustomerGroup,
    CustomerReturn,
    DeliveryRun,
    DeliveryStop,
    Invoice,
    PriceList,
    PriceListEntry,
    SalesOrder,
    SalesOrderLine,
    SalesOrderPick,
    TradeCustomer,
)
from .pricing import resolve_prices
from .serializers import (
    CancelOrderSerializer,
    CreditNoteSerializer,
    CustomerGroupSerializer,
    CustomerReturnSerializer,
    DeliverSerializer,
    DeliveryRunSerializer,
    FailStopSerializer,
    InvoiceSerializer,
    LaterReturnSerializer,
    PickRequestSerializer,
    PriceListSerializer,
    ResolveRequestSerializer,
    ResolveResponseSerializer,
    SalesOrderLineSerializer,
    SalesOrderPickSerializer,
    SalesOrderSerializer,
    SubstituteRequestSerializer,
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


class MayPick(BasePermission):
    """Staff, and the warehouse's stock operators (G42); other operators are stopped earlier by ``role_access``."""

    def has_permission(self, request, view):
        user = request.user
        return bool(getattr(user, 'company_id', None)) and (
            user.is_staff or user.is_superuser or (user.is_operator and user.role == STOCK))


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

    queryset = SalesOrder.objects.select_related('customer').prefetch_related(Prefetch(
        'lines', queryset=SalesOrderLine.objects.select_related('product').prefetch_related(Prefetch(
            'reservations', queryset=StockReservation.objects.filter(released_at__isnull=True),
            to_attr='active_reservations'), 'picks__batch'),
    ))
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

    # Picking (G42) is for staff and the stock operator role (also listed in role_access). Approving a swap stays
    # staff only (and never the proposer: wholesale.picking.decide).
    PICKING_ACTIONS = {'start_pick', 'finish_pick', 'picks', 'pick', 'substitutions', 'pick_list'}

    def get_permissions(self):
        if self.action in self.PICKING_ACTIONS:
            return [MayPick()]
        return super().get_permissions()

    def _moved(self, step, *args, **kwargs):
        try:
            result = step(self.get_object(), *args, **kwargs)
        except orders.OrderStateError as error:
            raise ValidationError({'detail': error.detail}) from None
        if isinstance(result, SalesOrder):
            return Response(self.get_serializer(self.get_queryset().get(pk=result.pk)).data)
        return result

    def _order(self, order):
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data)

    @extend_schema(request=None, responses=SalesOrderSerializer)
    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """Fix each line's price (lists valid on the delivery date, then the wholesale price) and confirm. Refused
        when a line has no trade price or the customer is on hold or closed."""
        return self._moved(orders.confirm, self.request.user)

    @extend_schema(request=None, responses=SalesOrderSerializer)
    @action(detail=True, methods=['post'])
    def allocate(self, request, pk=None):
        """Top up the short lines of a confirmed (or picking) order from free warehouse stock, FEFO (G41)."""
        return self._moved(orders.allocate, self.request.user)

    @extend_schema(request=CancelOrderSerializer, responses=SalesOrderSerializer)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a draft, confirmed or picking order, with a reason; its reserved stock is released."""
        serializer = CancelOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._moved(orders.cancel, self.request.user, serializer.validated_data['reason'])

    @extend_schema(request=None, responses=SalesOrderSerializer)
    @action(detail=True, methods=['post'], url_path='start-pick', url_name='start-pick')
    def start_pick(self, request, pk=None):
        """Start picking a confirmed order (G42)."""
        return self._moved(picking.start_pick)

    @extend_schema(request=None, responses=SalesOrderSerializer)
    @action(detail=True, methods=['post'], url_path='finish-pick', url_name='finish-pick')
    def finish_pick(self, request, pk=None):
        """Finish the pick: release the reservations and move every pick to DISPATCH at its real count and kg.
        Refused while a swap waits for approval or nothing is picked."""
        return self._moved(picking.finish_pick, request.user)

    @extend_schema(methods=['GET'], request=None, responses=SalesOrderPickSerializer(many=True))
    @extend_schema(methods=['POST'], request=PickRequestSerializer, responses={201: SalesOrderPickSerializer})
    @action(detail=True, methods=['get', 'post'])
    def picks(self, request, pk=None):
        """The order's picks; POST records one: the line and a lot number or a scanned label, with the units
        (pieces) and the real kg as the product needs."""
        order = self.get_object()
        if request.method == 'GET':
            rows = SalesOrderPick.objects.filter(line__order=order).select_related('batch')
            return Response(SalesOrderPickSerializer(rows, many=True).data)
        serializer = PickRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        line = data.pop('line')
        try:
            pick = picking.record_pick(order, line, user=request.user, **data)
        except orders.OrderStateError as error:
            raise ValidationError({'detail': error.detail}) from None
        return Response(SalesOrderPickSerializer(pick).data, status=201)

    @extend_schema(request=None, responses={204: None})
    @action(detail=True, methods=['delete'], url_path=r'picks/(?P<pick_id>\d+)', url_name='pick')
    def pick(self, request, pk=None, pick_id=None):
        """Take a pick back (until the pick is finished)."""
        try:
            picking.remove_pick(self.get_object(), int(pick_id))
        except orders.OrderStateError as error:
            raise ValidationError({'detail': error.detail}) from None
        return Response(status=204)

    @extend_schema(request=SubstituteRequestSerializer, responses={201: SalesOrderLineSerializer})
    @action(detail=True, methods=['post'])
    def substitutions(self, request, pk=None):
        """Propose another product for a line; another staff user approves or rejects it."""
        order = self.get_object()
        serializer = SubstituteRequestSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        try:
            line = picking.propose_substitute(order, data.pop('line'), data.pop('product'), user=request.user,
                                              **data)
        except orders.OrderStateError as error:
            raise ValidationError({'detail': error.detail}) from None
        return Response(SalesOrderLineSerializer(line, context=self.get_serializer_context()).data, status=201)

    def _decide(self, line_id, approve):
        order = self.get_object()
        try:
            picking.decide(order, int(line_id), approve=approve, user=self.request.user)
        except orders.OrderStateError as error:
            raise ValidationError({'detail': error.detail}) from None
        return self._order(order)

    @extend_schema(request=None, responses=SalesOrderSerializer)
    @action(detail=True, methods=['post'], url_path=r'substitutions/(?P<line_id>\d+)/approve',
            url_name='approve-substitution')
    def approve_substitution(self, request, pk=None, line_id=None):
        """Approve a proposed swap (staff, not the proposer): it is priced on the delivery date."""
        return self._decide(line_id, True)

    @extend_schema(request=None, responses=SalesOrderSerializer)
    @action(detail=True, methods=['post'], url_path=r'substitutions/(?P<line_id>\d+)/reject',
            url_name='reject-substitution')
    def reject_substitution(self, request, pk=None, line_id=None):
        """Reject a proposed swap (staff, not the proposer)."""
        return self._decide(line_id, False)

    @extend_schema(request=LaterReturnSerializer, responses={201: CustomerReturnSerializer})
    @action(detail=True, methods=['post'])
    def returns(self, request, pk=None):
        """Record goods the customer gave back after delivery (G45): to stock or waste by reason, at once."""
        order = self.get_object()
        serializer = LaterReturnSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        lines = [dict(line) for line in serializer.validated_data['lines']]
        for line in lines:
            location = line.get('location')
            if location is not None and location.company_id != order.company_id:
                raise ValidationError({'lines': "Another company's location can't take stock."})
        try:
            customer_return = returns.record_return(order, request.user, lines,
                                                    note=serializer.validated_data.get('note', ''))
        except returns.ReturnError as error:
            raise ValidationError({'detail': str(error)}) from None
        return Response(CustomerReturnSerializer(customer_return).data, status=201)

    @extend_schema(request=None, responses={(200, 'application/pdf'): OpenApiTypes.BINARY})
    @action(detail=True, methods=['get'], url_path='pick-list', url_name='pick-list')
    def pick_list(self, request, pk=None):
        """The printable pick list (PDF), one table per zone: ambient, chilled, frozen, then butchery."""
        order = self.get_object()
        response = HttpResponse(build_pick_list(order), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="pick-list-{order.number_display}.pdf"'
        return response


INVOICE_STATUSES = {value for value, _label in Invoice.STATUSES}


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('status', str, enum=sorted(INVOICE_STATUSES), description='Only drafts or issued invoices.'),
    OpenApiParameter('customer', int, description="Only this trade customer's invoices."),
    OpenApiParameter('order', int, description="Only this order's invoice."),
]))
class InvoiceViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    Trade invoices (G43). POST ``{order}`` makes a draft from a picked order, billed on what was picked (real kg for
    weighed goods); ``issue`` numbers it (gap-free per company), freezes it and emails the PDF. Only drafts are
    deleted or rebuilt. Members read; staff write.
    """

    queryset = Invoice.objects.select_related('order').prefetch_related('lines')
    serializer_class = InvoiceSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        status = params.get('status')
        if status is not None:
            if status not in INVOICE_STATUSES:
                raise ValidationError({'status': 'draft or issued.'})
            queryset = queryset.filter(status=status)
        for name, lookup in (('customer', 'order__customer_id'), ('order', 'order_id')):
            value = _id_param(params, name)
            if value is not None:
                queryset = queryset.filter(**{lookup: value})
        return queryset

    def _done(self, step, *args):
        try:
            invoice = step(*args)
        except invoices.InvoiceError as error:
            raise ValidationError({'detail': str(error)}) from None
        return Response(self.get_serializer(self.get_queryset().get(pk=invoice.pk)).data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = self._done(invoices.create_invoice, serializer.validated_data['order'], request.user)
        response.status_code = 201
        return response

    def perform_destroy(self, instance):
        try:
            invoices.discard_draft(instance)
        except invoices.InvoiceError as error:
            raise ValidationError({'detail': str(error)}) from None

    @extend_schema(request=None, responses=InvoiceSerializer)
    @action(detail=True, methods=['post'])
    def issue(self, request, pk=None):
        """Number, freeze and email the invoice. Refused without your company's VAT number."""
        return self._done(invoices.issue, self.get_object(), request.user)

    @extend_schema(request=None, responses=InvoiceSerializer)
    @action(detail=True, methods=['post'])
    def refresh(self, request, pk=None):
        """Rebuild a draft from the order's picked amounts as they are now."""
        return self._done(invoices.refresh_draft, self.get_object())

    @extend_schema(request=None, responses=InvoiceSerializer)
    @action(detail=True, methods=['post'])
    def email(self, request, pk=None):
        """Send an issued invoice's PDF to the customer again."""
        invoice = self.get_object()

        def queue(invoice):
            invoices.resend(invoice)
            return invoice

        return self._done(queue, invoice)

    @extend_schema(request=None, responses={(200, 'application/pdf'): OpenApiTypes.BINARY})
    @action(detail=True, methods=['get'])
    def pdf(self, request, pk=None):
        """The invoice as a PDF (a draft says DRAFT and has no number)."""
        invoice = self.get_object()
        response = HttpResponse(build_invoice_pdf(invoice), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{invoice.number_display}.pdf"'
        return response


RUN_STATUSES = {value for value, _label in DeliveryRun.STATUSES}


def _is_driver(user):
    return bool(getattr(user, 'is_operator', False)) and user.role == DRIVER


class MayRecordDelivery(BasePermission):
    """Staff, and drivers (only their own runs are in their queryset, so any other is a 404)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(getattr(user, 'company_id', None)) and (user.is_staff or user.is_superuser or _is_driver(user))


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('status', str, enum=sorted(RUN_STATUSES), description='Only runs in this status.'),
    OpenApiParameter('date', OpenApiTypes.DATE, description='Only runs on this day.'),
]))
class DeliveryRunViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                         mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """
    Delivery runs (G44): a date, a van, a driver and picked orders as stops in driving order. ``start`` moves the
    orders from DISPATCH onto the van; each stop is delivered (proof of delivery) or failed; ``complete`` brings
    stops not delivered back to DISPATCH. Staff plan and move runs; members read; a driver (operator role) sees only
    their own runs and records their stops. No delete: cancel a planned run.
    """

    queryset = DeliveryRun.objects.select_related('vehicle', 'driver').prefetch_related('stops__order__customer')
    serializer_class = DeliveryRunSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]
    POD_ACTIONS = {'deliver', 'fail', 'photo'}

    def get_permissions(self):
        if self.action in self.POD_ACTIONS:
            return [MayRecordDelivery()]
        return super().get_permissions()

    def get_queryset(self):
        queryset = super().get_queryset()
        if _is_driver(self.request.user):
            queryset = queryset.filter(driver=self.request.user)
        params = self.request.query_params
        status = params.get('status')
        if status is not None:
            if status not in RUN_STATUSES:
                raise ValidationError({'status': f'One of {", ".join(sorted(RUN_STATUSES))}.'})
            queryset = queryset.filter(status=status)
        day = _date_param(params, 'date')
        if day is not None:
            queryset = queryset.filter(run_date=day)
        return queryset

    def _done(self, step, *args, **kwargs):
        try:
            step(*args, **kwargs)
        except runs.RunError as error:
            raise ValidationError({'detail': str(error)}) from None
        run = self.get_queryset().get(pk=self.kwargs['pk'])
        return Response(self.get_serializer(run).data)

    def _stop(self, stop_id):
        return get_object_or_404(DeliveryStop, pk=stop_id, run=self.get_object())

    @extend_schema(request=None, responses=DeliveryRunSerializer)
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """Take the run out: its orders move from DISPATCH onto the van and become dispatched."""
        return self._done(runs.start_run, self.get_object(), request.user)

    @extend_schema(request=None, responses=DeliveryRunSerializer)
    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Close the run once every stop is recorded; stops not delivered go back to DISPATCH."""
        return self._done(runs.complete_run, self.get_object(), request.user)

    @extend_schema(request=None, responses=DeliveryRunSerializer)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a planned run; its orders can go on another."""
        return self._done(runs.cancel_run, self.get_object(), request.user)

    @extend_schema(request=DeliverSerializer, responses=DeliveryRunSerializer)
    @action(detail=True, methods=['post'], url_path=r'stops/(?P<stop_id>\d+)/deliver', url_name='deliver',
            parser_classes=[MultiPartParser, FormParser, JSONParser])
    def deliver(self, request, pk=None, stop_id=None):
        """Proof of delivery: who received it, when, notes, an optional photo. The order's stock goes to the
        customer and the order is delivered (invoiced if its invoice is issued)."""
        stop = self._stop(stop_id)
        serializer = DeliverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._done(runs.deliver, stop, request.user, **serializer.validated_data)

    @extend_schema(request=FailStopSerializer, responses=DeliveryRunSerializer)
    @action(detail=True, methods=['post'], url_path=r'stops/(?P<stop_id>\d+)/fail', url_name='fail')
    def fail(self, request, pk=None, stop_id=None):
        """Record a stop as not delivered, with the reason; its stock stays on the van until the run is completed."""
        stop = self._stop(stop_id)
        serializer = FailStopSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._done(runs.fail_stop, stop, request.user, serializer.validated_data['reason'])

    @extend_schema(request=None, responses={(200, 'image/*'): OpenApiTypes.BINARY})
    @action(detail=True, methods=['get'], url_path=r'stops/(?P<stop_id>\d+)/photo', url_name='photo')
    def photo(self, request, pk=None, stop_id=None):
        """A stop's proof-of-delivery photo (staff and the run's driver)."""
        stop = self._stop(stop_id)
        if not stop.photo:
            raise Http404('No photo for this stop.')
        extension = stop.photo.name.rsplit('.', 1)[-1].lower()
        return FileResponse(stop.photo.open('rb'), content_type='image/png' if extension == 'png' else 'image/jpeg',
                            filename=f'{run_label(stop)}.{extension}')

    @extend_schema(request=None, responses={(200, 'application/pdf'): OpenApiTypes.BINARY})
    @action(detail=True, methods=['get'])
    def manifest(self, request, pk=None):
        """The run's manifest (PDF): stops in driving order with addresses, goods and signature boxes."""
        run = self.get_object()
        response = HttpResponse(build_manifest_pdf(run), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="manifest-{run.number_display}.pdf"'
        return response


def run_label(stop):
    return f'pod-{stop.run.number_display}-stop-{stop.position}'


RETURN_KINDS = {value for value, _label in CustomerReturn.KINDS}


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('order', int, description="Only this order's returns."),
    OpenApiParameter('kind', str, enum=sorted(RETURN_KINDS), description='door or later.'),
]))
class CustomerReturnViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                            viewsets.GenericViewSet):
    """Goods given back (G45): refusals recorded at the door and returns recorded on the order. Members read."""

    queryset = CustomerReturn.objects.select_related('order').prefetch_related('lines__order_line__product')
    serializer_class = CustomerReturnSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        order = _id_param(params, 'order')
        if order is not None:
            queryset = queryset.filter(order_id=order)
        kind = params.get('kind')
        if kind is not None:
            if kind not in RETURN_KINDS:
                raise ValidationError({'kind': 'door or later.'})
            queryset = queryset.filter(kind=kind)
        return queryset


class CreditNoteViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                        mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    Credit notes (G45). POST ``{customer_return}`` drafts one against the order's issued invoice; ``issue`` numbers
    (``CN-``, gap-free per company), freezes and emails it. Only drafts are deleted. Members read; staff write.
    """

    queryset = CreditNote.objects.select_related('invoice').prefetch_related('lines')
    serializer_class = CreditNoteSerializer
    permission_classes = [CompanyMemberReadsStaffWrites]

    def _done(self, step, *args):
        try:
            note = step(*args)
        except credit_notes.CreditNoteError as error:
            raise ValidationError({'detail': str(error)}) from None
        note = note or self.get_object()
        return Response(self.get_serializer(self.get_queryset().get(pk=note.pk)).data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = self._done(credit_notes.create_credit_note, serializer.validated_data['customer_return'],
                              request.user)
        response.status_code = 201
        return response

    def perform_destroy(self, instance):
        try:
            credit_notes.discard_draft(instance)
        except credit_notes.CreditNoteError as error:
            raise ValidationError({'detail': str(error)}) from None

    @extend_schema(request=None, responses=CreditNoteSerializer)
    @action(detail=True, methods=['post'])
    def issue(self, request, pk=None):
        """Number, freeze and email the credit note; refused if it would credit more than was invoiced."""
        return self._done(credit_notes.issue_credit_note, self.get_object(), request.user)

    @extend_schema(request=None, responses=CreditNoteSerializer)
    @action(detail=True, methods=['post'])
    def email(self, request, pk=None):
        """Send an issued credit note's PDF to the customer again."""
        return self._done(credit_notes.resend, self.get_object())

    @extend_schema(request=None, responses={(200, 'application/pdf'): OpenApiTypes.BINARY})
    @action(detail=True, methods=['get'])
    def pdf(self, request, pk=None):
        """The credit note as a PDF."""
        note = self.get_object()
        response = HttpResponse(build_credit_note_pdf(note), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{note.number_display}.pdf"'
        return response
