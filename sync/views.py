# sync/views.py

import json
import logging
import uuid

from django.db import IntegrityError, transaction
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenancy import HasCompany, IsCompanyStaffOrReadOnly, TenantScopedMixin
from inventory.ledger import online_locations, take_fefo, virtual_location
from inventory.models import Location, Product

from . import loyalty
from .models import InboundEvent, ShopifyConnection, StorefrontConnection, SyncTask
from .outbox import active_connection
from .serializers import (
    LoyaltyCustomerSerializer, LoyaltyLookupSerializer, ShopifyConnectionSerializer, SyncTaskSerializer,
)
from .signing import verify

logger = logging.getLogger(__name__)


class SyncTaskViewSet(
    TenantScopedMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    """Sync runs. The id, status and timings are set by the system, so a task cannot be edited."""

    queryset = SyncTask.objects.all()
    serializer_class = SyncTaskSerializer


class ShopifyConnectionViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """The company's Shopify store. Anyone in the company can see it; only staff can connect, change or remove it."""

    permission_classes = [IsCompanyStaffOrReadOnly]
    queryset = ShopifyConnection.objects.all()
    serializer_class = ShopifyConnectionSerializer


UNAUTHORISED = {'code': 'invalid_signature', 'message': 'Invalid signature.', 'status_code': 401}


def _parse_items(items):
    """Split ``order.paid`` items into ``(product_id, quantity)`` pairs and a list of problems for bad entries."""
    good, problems = [], []
    for item in items if isinstance(items, list) else []:
        product_id = item.get('product_id') if isinstance(item, dict) else None
        quantity = item.get('quantity') if isinstance(item, dict) else None
        if not isinstance(product_id, str) or not product_id or type(quantity) is not int or quantity < 1:
            problems.append({'product_id': product_id if isinstance(product_id, str) else None,
                             'requested': quantity if type(quantity) is int else None,
                             'taken': 0, 'reason': 'invalid_item'})
        else:
            good.append((product_id, quantity))
    return good, problems


def _apply_order_paid(company, data):
    """Take the order's units from stock (FEFO). Returns ``(status, detail)``; never raises on stock problems."""
    items, detail = _parse_items(data.get('items') if isinstance(data, dict) else None)
    if not items and not detail:
        detail.append({'product_id': None, 'requested': None, 'taken': 0, 'reason': 'no_items'})
    ids = {product_id for product_id, _ in items}
    # Lock products in pk order, as the till does, so a web order and a till sale cannot deadlock.
    products = {
        p.external_id: p
        for p in Product.objects.select_for_update().filter(company=company, external_id__in=ids).order_by('pk')
    }
    unmatched = bool(detail)
    partial = False
    # Web orders take what the shop offers online: the online-sellable locations.
    selling_from = list(online_locations(company))
    customer = virtual_location(company, Location.CUSTOMER)
    for product_id, quantity in sorted(items, key=lambda i: products[i[0]].pk if i[0] in products else 0):
        product = products.get(product_id)
        if product is None:
            unmatched = True
            detail.append({'product_id': product_id, 'requested': quantity, 'taken': 0, 'reason': 'unknown_product'})
            continue
        allocations, shortfall = take_fefo(product, quantity, locations=selling_from, destination=customer)
        if shortfall:
            partial = True
            detail.append({'product_id': product_id, 'requested': quantity,
                           'taken': quantity - shortfall, 'reason': 'short_stock'})
    return ('unmatched' if unmatched else 'partial' if partial else 'applied'), detail


class StorefrontInboundView(APIView):
    """
    Signed webhooks from the m18 storefront (``POST /sync/inbound/storefront/<connection_id>/``).

    There is no user: the ``X-Integration-Signature`` over the raw body, checked with the connection's
    secret, is the only authentication, so operators never call this and it is not in ``role_access``.
    A missing or inactive connection and a bad signature get the same 401. Any validly signed event gets
    200, even when it cannot be fully applied (the order is already paid, so retrying would never help);
    problems are recorded on the ``InboundEvent``. A repeated ``event_id`` is a no-op.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)  # machine-to-machine; the contract is in the m18 integration docs
    def post(self, request, connection_id):
        body = request.body
        connection = StorefrontConnection.objects.filter(pk=connection_id, is_active=True).select_related('company').first()
        secret = connection.webhook_secret if connection else None
        if not verify(secret, body, request.headers.get('X-Integration-Signature')):
            return Response(UNAUTHORISED, status=401)

        try:
            envelope = json.loads(body)
            event_id = uuid.UUID(str(envelope['event_id']))
            event_type = envelope['event_type']
            if not isinstance(event_type, str) or not event_type or len(event_type) > 40:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            return Response({'code': 'invalid_event', 'message': 'Malformed event envelope.', 'status_code': 400}, status=400)

        try:
            with transaction.atomic():
                event = InboundEvent.objects.create(
                    connection=connection, event_id=event_id, event_type=event_type,
                    payload=envelope, status='unmatched',
                )
                if event_type == 'order.paid':
                    event.status, event.detail = _apply_order_paid(connection.company, envelope.get('data'))
                else:
                    event.detail = [{'reason': 'unknown_event_type'}]
                event.save(update_fields=['status', 'detail'])
        except IntegrityError:
            if InboundEvent.objects.filter(connection=connection, event_id=event_id).exists():
                return Response({'status': 'duplicate'})
            raise
        if event.status != 'applied':
            logger.warning('Storefront event %s (%s) was %s: %s', event_id, event_type, event.status, event.detail)
        return Response({'status': event.status})


class LoyaltyLookupView(APIView):
    """
    ``POST /sync/loyalty/lookup/`` with ``{"barcode"}``: the card holder's first name and points, from the storefront.

    Uses the signed-in user's company's storefront connection. Gives up after 1.5 s (503), so the till can sell
    without points; 404 for an unknown card, 409 when the company has no active storefront connection.
    """

    permission_classes = [IsAuthenticated, HasCompany]

    @extend_schema(request=LoyaltyLookupSerializer, responses={200: LoyaltyCustomerSerializer})
    def post(self, request):
        serializer = LoyaltyLookupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = active_connection(request.user.company_id) if request.user.company_id else None
        if connection is None:
            return Response(
                {'code': 'not_configured', 'message': 'Loyalty is not connected for this company.', 'status_code': 409},
                status=409,
            )
        try:
            customer = loyalty.lookup(connection, serializer.validated_data['barcode'])
        except loyalty.LoyaltyUnavailable:
            return Response(
                {'code': 'loyalty_unavailable', 'message': 'Loyalty is unavailable right now.', 'status_code': 503},
                status=503,
            )
        if customer is None:
            return Response({'code': 'not_found', 'message': 'No loyalty account for this card.', 'status_code': 404},
                            status=404)
        return Response(customer)
