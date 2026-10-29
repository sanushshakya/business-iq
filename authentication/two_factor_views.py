# authentication/two_factor_views.py

"""HTTP side of two-factor sign-in. The rules live in ``two_factor.py``; these views only translate."""

from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import refresh_tokens, two_factor
from .models import TwoFactorChallenge
from .serializers import (
    TwoFactorCodeSerializer,
    TwoFactorEnrolConfirmSerializer,
    TwoFactorEnrolSerializer,
    TokenPairResponse,
    TwoFactorVerifySerializer,
)

TwoFactorChallengeResponse = inline_serializer(
    'TwoFactorChallenge',
    {
        'two_factor': drf_serializers.ChoiceField(
            choices=[TwoFactorChallenge.VERIFY, TwoFactorChallenge.ENROL],
            help_text='verify: post a code to /auth/2fa/verify/. enrol: set up an authenticator at /auth/2fa/enrol/.',
        ),
        'challenge': drf_serializers.CharField(),
        'expires_in': drf_serializers.IntegerField(),
    },
)

EnrolmentResponse = inline_serializer(
    'TwoFactorEnrolment',
    {
        'secret': drf_serializers.CharField(help_text='For typing into the app by hand. Shown this once.'),
        'otpauth_uri': drf_serializers.CharField(),
        'qr_svg': drf_serializers.CharField(help_text='The otpauth URI as an SVG QR code.'),
    },
)

EnrolConfirmResponse = inline_serializer(
    'TwoFactorEnrolConfirmed',
    {
        'recovery_codes': drf_serializers.ListField(
            child=drf_serializers.CharField(), help_text='10 single-use codes. Shown this once.'
        ),
        'access_token': drf_serializers.CharField(required=False, help_text='Only when enrolled with a challenge.'),
        'refresh_token': drf_serializers.CharField(required=False),
        'token_type': drf_serializers.CharField(required=False),
        'expires_in': drf_serializers.IntegerField(required=False),
        'refresh_expires_in': drf_serializers.IntegerField(required=False),
    },
)

StatusResponse = inline_serializer(
    'TwoFactorStatus',
    {
        'enabled': drf_serializers.BooleanField(),
        'required': drf_serializers.BooleanField(help_text='Admins must keep two-factor sign-in on.'),
        'recovery_codes_left': drf_serializers.IntegerField(),
    },
)

MESSAGES = {
    'invalid_code': _('That code is not right. Check the time on your phone and try again.'),
    'already_enabled': _('Two-factor sign-in is already on.'),
    'not_started': _('Start the set-up first.'),
    'required': _('Admins must keep two-factor sign-in on.'),
}


def _error(code, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': MESSAGES[code], 'code': code}, status=http_status)


def _sign_in_again():
    return Response(
        {'detail': _('This sign-in has expired. Please sign in again.'), 'code': 'challenge_invalid'},
        status=status.HTTP_401_UNAUTHORIZED,
    )


def _wrong_code(user):
    if two_factor.is_locked(user):
        return Response(
            {'detail': _('Too many wrong codes. Wait a few minutes and try again.'), 'code': 'locked'},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    return _error('invalid_code')


def login_response(user):
    """What ``/auth/login/`` answers once the password is right: tokens, or a challenge for the second step."""
    if two_factor.is_enabled(user):
        purpose = TwoFactorChallenge.VERIFY
    elif two_factor.is_required(user):
        purpose = TwoFactorChallenge.ENROL
    else:
        return refresh_tokens.issue_token_pair(user)
    return {
        'two_factor': purpose,
        'challenge': two_factor.issue_challenge(user, purpose),
        'expires_in': two_factor.CHALLENGE_TTL_SECONDS,
    }


class TwoFactorThrottled:
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'two_factor'


class TwoFactorVerifyView(TwoFactorThrottled, APIView):
    """
    Second step of signing in: trade the ``challenge`` from ``/auth/login/`` and a 6-digit ``code`` (or one of the
    ``recovery_code``\\ s) for the token pair. A wrong code is 400; after 5 the challenge is dead (401, sign in again).
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=TwoFactorVerifySerializer, responses=TokenPairResponse)
    def post(self, request):
        serializer = TwoFactorVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            challenge = two_factor.load_challenge(data['challenge'], TwoFactorChallenge.VERIFY)
        except two_factor.InvalidChallenge:
            return _sign_in_again()
        user = challenge.user
        if data.get('code'):
            ok = two_factor.check_code(user, data['code'])
        else:
            ok = two_factor.use_recovery_code(user, data['recovery_code'])
        if not ok:
            two_factor.record_failure(challenge)
            return _wrong_code(user)
        if not two_factor.consume(challenge):
            return _sign_in_again()
        return Response(refresh_tokens.issue_token_pair(user))


def _enrolling_user(request, data):
    """
    ``(user, challenge)`` for an enrolment request: the enrol challenge from ``/auth/login/`` (an admin who must set
    it up first), or the signed-in user. Raises ``InvalidChallenge`` when neither applies.
    """
    if data.get('challenge'):
        challenge = two_factor.load_challenge(data['challenge'], TwoFactorChallenge.ENROL)
        return challenge.user, challenge
    if request.user and request.user.is_authenticated:
        return request.user, None
    raise two_factor.InvalidChallenge()


class TwoFactorEnrolView(TwoFactorThrottled, APIView):
    """
    Start setting up an authenticator app: returns the secret, the ``otpauth://`` URI and a QR code to scan. Send
    the ``challenge`` from ``/auth/login/`` when signing in requires it, or call it signed in. Confirm with
    ``/auth/2fa/enrol/confirm/``.
    """

    permission_classes = [AllowAny]

    @extend_schema(request=TwoFactorEnrolSerializer, responses=EnrolmentResponse)
    def post(self, request):
        serializer = TwoFactorEnrolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user, _challenge = _enrolling_user(request, serializer.validated_data)
        except two_factor.InvalidChallenge:
            return _sign_in_again()
        try:
            return Response(two_factor.start_enrolment(user))
        except two_factor.TwoFactorError as error:
            return _error(error.code)


class TwoFactorEnrolConfirmView(TwoFactorThrottled, APIView):
    """
    Finish setting up: a code from the app switches two-factor sign-in on and returns 10 recovery codes (shown
    once). When this was done with a sign-in ``challenge``, the token pair comes back too.
    """

    permission_classes = [AllowAny]

    @extend_schema(request=TwoFactorEnrolConfirmSerializer, responses=EnrolConfirmResponse)
    def post(self, request):
        serializer = TwoFactorEnrolConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user, challenge = _enrolling_user(request, serializer.validated_data)
        except two_factor.InvalidChallenge:
            return _sign_in_again()
        try:
            codes = two_factor.confirm_enrolment(user, serializer.validated_data['code'])
        except two_factor.TwoFactorError as error:
            if challenge is not None and error.code == 'invalid_code':
                two_factor.record_failure(challenge)
            return _wrong_code(user) if error.code == 'invalid_code' else _error(error.code)
        body = {'recovery_codes': codes}
        if challenge is not None:
            if not two_factor.consume(challenge):
                return _sign_in_again()
            body.update(refresh_tokens.issue_token_pair(user))
        return Response(body)


class TwoFactorStatusView(APIView):
    """Whether two-factor sign-in is on for the signed-in user, whether they must keep it, and recovery codes left."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=StatusResponse)
    def get(self, request):
        user = request.user
        return Response({
            'enabled': two_factor.is_enabled(user),
            'required': two_factor.is_required(user),
            'recovery_codes_left': two_factor.recovery_codes_left(user),
        })


class TwoFactorDisableView(TwoFactorThrottled, APIView):
    """Turn two-factor sign-in off with a current code. Not allowed for admins."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=TwoFactorCodeSerializer, responses={204: None})
    def post(self, request):
        serializer = TwoFactorCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            two_factor.disable(request.user, serializer.validated_data['code'])
        except two_factor.TwoFactorError as error:
            return _wrong_code(request.user) if error.code == 'invalid_code' else _error(error.code)
        return Response(status=status.HTTP_204_NO_CONTENT)
