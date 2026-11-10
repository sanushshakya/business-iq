from django.db.models import Prefetch
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenancy import TenantScopedMixin
from inventory.views import CompanyMemberReadsStaffWrites

from datetime import timedelta

from vat.services import uk_date

from . import journal as posting
from .postings import run_postings
from .reports import trial_balance
from .models import Account, Journal, JournalLine, Period
from .serializers import (
    AccountSerializer,
    JournalCreateSerializer,
    JournalSerializer,
    LockSerializer,
    LockStateSerializer,
    PeriodSerializer,
    PostingRunRequestSerializer,
    PostingRunSerializer,
    ReopenSerializer,
    ReverseSerializer,
    StrictDateField,
    TrialBalanceSerializer,
)


class OwnCompanyMixin(TenantScopedMixin):
    """The user's own company's books only, superusers included; members read, staff write."""

    permission_classes = [CompanyMemberReadsStaffWrites]

    def get_queryset(self):
        return super().get_queryset().filter(company_id=self.request.user.company_id)


class AccountViewSet(OwnCompanyMixin, mixins.ListModelMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                     mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """The chart of accounts (G52). Never deleted: deactivate one (not an account automatic postings use). Its
    code and type are fixed once something is posted to it."""

    queryset = Account.objects.all()
    serializer_class = AccountSerializer


def _strict_date(params, name):
    raw = params.get(name)
    if raw is None:
        return None
    try:
        return StrictDateField().run_validation(raw)
    except Exception:  # noqa: BLE001 - any parse failure is the same 400
        raise ValidationError({name: 'A date as YYYY-MM-DD.'}) from None


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('from', OpenApiTypes.DATE), OpenApiParameter('to', OpenApiTypes.DATE),
    OpenApiParameter('account', int, description='Only journals with a line on this account.'),
    OpenApiParameter('source', str, description='manual, reversal, or an automatic source.'),
]))
class JournalViewSet(OwnCompanyMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Journals (G52), newest first: balanced, numbered, never changed or deleted. Staff post manual ones and
    reverse any journal (once) with a reason."""

    queryset = Journal.objects.select_related('reversed_by').prefetch_related(
        Prefetch('lines', queryset=JournalLine.objects.select_related('account')))
    serializer_class = JournalSerializer

    def get_queryset(self):
        queryset = super().get_queryset().order_by('-date', '-number')
        params = self.request.query_params
        start, end = _strict_date(params, 'from'), _strict_date(params, 'to')
        if start:
            queryset = queryset.filter(date__gte=start)
        if end:
            queryset = queryset.filter(date__lte=end)
        if params.get('account'):
            try:
                queryset = queryset.filter(lines__account_id=int(params['account'])).distinct()
            except ValueError:
                raise ValidationError({'account': 'An account id.'}) from None
        if params.get('source'):
            queryset = queryset.filter(source=params['source'])
        return queryset

    def _answer(self, journal):
        return Response(self.get_serializer(self.get_queryset().get(pk=journal.pk)).data, status=201)

    @extend_schema(request=JournalCreateSerializer, responses={201: JournalSerializer})
    def create(self, request, *args, **kwargs):
        """Post a manual journal (staff)."""
        body = JournalCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        try:
            journal = posting.post_journal(request.user.company, date=data['date'], narrative=data['narrative'],
                                           lines=data['lines'], user=request.user)
        except posting.JournalError as error:
            raise ValidationError({'detail': error.detail}) from None
        return self._answer(journal)

    @extend_schema(request=ReverseSerializer, responses={201: JournalSerializer})
    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):
        """Reverse a journal (staff): every line's sides swapped, dated `date` (default today), once."""
        journal = self.get_object()
        body = ReverseSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            reversal = posting.reverse_journal(journal, request.user, body.validated_data['reason'],
                                               date=body.validated_data.get('date'))
        except posting.JournalError as error:
            raise ValidationError({'detail': error.detail}) from None
        return self._answer(reversal)


class PeriodViewSet(OwnCompanyMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Calendar months (G52). A locked month takes nothing, nor does any month before it."""

    queryset = Period.objects.all()
    serializer_class = PeriodSerializer
    pagination_class = None

    def _state(self):
        company = self.request.user.company
        return Response(LockStateSerializer({'locked_through': posting.locked_through(company),
                                             'periods': Period.objects.filter(company=company)}).data)

    def _run(self, change):
        try:
            change()
        except posting.JournalError as error:
            raise ValidationError({'detail': error.detail}) from None
        return self._state()

    @extend_schema(request=LockSerializer, responses=LockStateSerializer)
    @action(detail=False, methods=['post'])
    def lock(self, request):
        """Lock a month and every month before it (staff)."""
        body = LockSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return self._run(lambda: posting.lock_period(request.user.company, body.validated_data['month'],
                                                     request.user))

    @extend_schema(request=ReopenSerializer, responses=LockStateSerializer)
    @action(detail=False, methods=['post'])
    def reopen(self, request):
        """Reopen a locked month and every locked month after it (staff), with a reason."""
        body = ReopenSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return self._run(lambda: posting.reopen_period(request.user.company, body.validated_data['month'],
                                                       request.user, body.validated_data['reason']))


class PostingRunView(APIView):
    """Post a range of UK days now (G53, staff): till takings and voids, customer ledger documents, waste and
    butchery loss. What was posted before is not posted again. The nightly run does yesterday by itself."""

    permission_classes = [CompanyMemberReadsStaffWrites]

    @extend_schema(request=PostingRunRequestSerializer, responses=PostingRunSerializer)
    def post(self, request):
        body = PostingRunRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        start, end = body.validated_data['from'], body.validated_data['to']
        if end >= uk_date():
            raise ValidationError({'to': 'Yesterday at the latest: a day is posted once it is over.'})
        if start > end:
            raise ValidationError({'from': 'On or before `to`.'})
        if end - start > timedelta(days=92):
            raise ValidationError({'from': 'At most 93 days at a time (the command posts longer ranges).'})
        return Response(PostingRunSerializer(run_postings(request.user.company, start, end, user=request.user)).data)


class TrialBalanceView(APIView):
    """The trial balance (G53): every account with something posted to the end of `on` (default: everything),
    with its debits and credits; the totals are equal."""

    permission_classes = [CompanyMemberReadsStaffWrites]

    @extend_schema(parameters=[OpenApiParameter('on', OpenApiTypes.DATE)], responses=TrialBalanceSerializer)
    def get(self, request):
        on = _strict_date(request.query_params, 'on')
        return Response(TrialBalanceSerializer(trial_balance(request.user.company, on=on)).data)
