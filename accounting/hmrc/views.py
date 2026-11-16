"""HMRC MTD routes (G56) under /accounting/hmrc/. Members read the status; staff connect, file and disconnect;
operators are not in role_access. The callback is HMRC's redirect: no token of ours, authenticated by its state."""

from django.conf import settings
from django.http import HttpResponseRedirect
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.models import HmrcConnection, HmrcSubmission
from common.tenancy import HasCompany
from inventory.views import CompanyMemberReadsStaffWrites

from . import client, filing, fraud, oauth
from .serializers import (AuthorizeUrlSerializer, HmrcErrorSerializer, HmrcStatusSerializer,
                          HmrcSubmissionSerializer, ObligationSerializer, ObligationsRequestSerializer,
                          SubmitRequestSerializer, FraudClientSerializer)

ACCOUNTS_PAGE = '/dashboard/accounts'


class HmrcStatusView(APIView):
    """Whether filing with HMRC is on, and whether this company is connected (never the tokens)."""

    permission_classes = [CompanyMemberReadsStaffWrites]

    @extend_schema(responses=HmrcStatusSerializer)
    def get(self, request):
        connection = HmrcConnection.objects.filter(company_id=request.user.company_id).first()
        return Response(HmrcStatusSerializer({
            'environment': settings.HMRC_VAT_ENVIRONMENT, 'connected': connection is not None,
            'vrn': connection.vrn if connection else None,
            'connected_at': connection.connected_at if connection else None}).data)


class HmrcConnectView(APIView):
    """Start connecting this company to HMRC: the browser is sent to the returned HMRC sign-in URL."""

    permission_classes = [CompanyMemberReadsStaffWrites]

    @extend_schema(request=None, responses=AuthorizeUrlSerializer)
    def post(self, request):
        try:
            url = oauth.start(request.user.company, request.user)
        except oauth.ConnectError as error:
            raise ValidationError({'detail': str(error)}) from None
        return Response({'authorize_url': url})


class HmrcCallbackView(APIView):
    """HMRC's redirect after sign-in: stores the connection and sends the browser back to the Accounts page."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)
    def get(self, request):
        outcome = oauth.finish(request.query_params)
        if outcome == 'connected':
            return HttpResponseRedirect(f'{ACCOUNTS_PAGE}?hmrc=connected')
        return HttpResponseRedirect(f'{ACCOUNTS_PAGE}?hmrc=error&reason={outcome}')


class HmrcConnectionView(APIView):
    """Disconnect: the stored HMRC tokens are deleted."""

    permission_classes = [CompanyMemberReadsStaffWrites]

    @extend_schema(responses={204: None})
    def delete(self, request):
        HmrcConnection.objects.filter(company_id=request.user.company_id).delete()
        return Response(status=204)


def hmrc_error_response(error):
    """HMRC said no: 400 when it is about what was sent (the user can act on it), 502 when HMRC failed."""
    status = 400 if error.status in (400, 401, 403, 404, 409, 422) else 502
    return Response({'code': 'hmrc_error', 'hmrc_code': error.code, 'message': error.message}, status=status)


def filing_error_response(error):
    return Response({'detail': error.detail}, status=409 if error.conflict else 400)


class HmrcObligationsView(APIView):
    """HMRC's VAT periods for the last 366 days (``status`` O open / F fulfilled), each with ``filed_in_iq``. A POST
    because the body carries the browser's details for HMRC's fraud-prevention headers (never in a URL)."""

    permission_classes = [HasCompany]

    @extend_schema(request=ObligationsRequestSerializer,
                   responses={200: ObligationSerializer(many=True), 400: HmrcErrorSerializer})
    def post(self, request):
        body = ObligationsRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            connection = filing.connection_for(request.user.company)
            rows = filing.obligations(connection, fraud.headers(request, body.validated_data['client'], request.user),
                                      status=body.validated_data.get('status'))
        except filing.FilingError as error:
            return filing_error_response(error)
        except client.HmrcError as error:
            return hmrc_error_response(error)
        return Response(ObligationSerializer(rows, many=True).data)


class HmrcReturnsView(APIView):
    """Submissions to HMRC, newest first (members read). Staff submit the return for an open obligation: 201
    accepted (filed and locked), 400 refused by HMRC (nothing changed), 202 no clear answer (check it)."""

    permission_classes = [CompanyMemberReadsStaffWrites]

    @extend_schema(responses=HmrcSubmissionSerializer(many=True))
    def get(self, request):
        rows = HmrcSubmission.objects.filter(company_id=request.user.company_id)
        return Response(HmrcSubmissionSerializer(rows, many=True).data)

    @extend_schema(request=SubmitRequestSerializer,
                   responses={201: HmrcSubmissionSerializer, 202: HmrcSubmissionSerializer,
                              400: HmrcErrorSerializer})
    def post(self, request):
        body = SubmitRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        try:
            submission = filing.submit(request.user.company, request.user, data['period_key'], data['start'],
                                       data['end'], fraud.headers(request, data['client'], request.user))
        except filing.FilingError as error:
            return filing_error_response(error)
        except client.HmrcError as error:
            return hmrc_error_response(error)
        return _submission_response(submission, created=True)


def _submission_response(submission, created=False):
    if submission.status == HmrcSubmission.REJECTED and created:
        return Response({'code': 'hmrc_error', 'hmrc_code': submission.error_code,
                         'message': submission.error_message, 'submission': submission.pk}, status=400)
    status = {HmrcSubmission.ACCEPTED: 201 if created else 200, HmrcSubmission.UNKNOWN: 202 if created else 200}
    return Response(HmrcSubmissionSerializer(submission).data, status=status.get(submission.status, 200))


class HmrcReturnCheckView(APIView):
    """Ask HMRC what it holds for an attempt with no clear answer, and finish or close it."""

    permission_classes = [CompanyMemberReadsStaffWrites]

    @extend_schema(request=FraudClientSerializer, responses={200: HmrcSubmissionSerializer, 400: HmrcErrorSerializer})
    def post(self, request, pk):
        submission = HmrcSubmission.objects.filter(company_id=request.user.company_id, pk=pk).first()
        if submission is None:
            return Response({'detail': 'Not found.'}, status=404)
        body = FraudClientSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            filing.check(submission, request.user, fraud.headers(request, body.validated_data['client'],
                                                                 request.user))
        except filing.FilingError as error:
            return filing_error_response(error)
        except client.HmrcError as error:
            return hmrc_error_response(error)
        return _submission_response(submission)
