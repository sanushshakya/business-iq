from django.db import IntegrityError, transaction
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError

from common.tenancy import TenantScopedMixin
from inventory.views import CompanyMemberReadsStaffWrites

from .models import TradeCustomer
from .serializers import TradeCustomerSerializer

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
