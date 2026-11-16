"""HMRC MTD routes (G56) under /accounting/hmrc/. Members read the status; staff connect, file and disconnect;
operators are not in role_access. The callback is HMRC's redirect: no token of ours, authenticated by its state."""

from django.conf import settings
from django.http import HttpResponseRedirect
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.models import HmrcConnection
from inventory.views import CompanyMemberReadsStaffWrites

from . import oauth
from .serializers import AuthorizeUrlSerializer, HmrcStatusSerializer

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
