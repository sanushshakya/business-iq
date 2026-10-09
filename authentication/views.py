"""
authentication/views.py

This file contains the views for handling password reset confirmations within a Django application.
"""

import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import mixins, status, viewsets
from authentication.models import UserInvitation
from common.tenancy import HasCompany, TenantScopedMixin
from authentication import refresh_tokens
from authentication.emails import send_invitation_email, send_password_reset_email
from authentication.serializers import (
    InvitationCreateSerializer,
    InvitationSerializer,
    LoginUserSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RefreshTokenSerializer,
)

User = get_user_model()

TokenPairResponse = inline_serializer(
    'TokenPairResponse',
    {
        'access_token': drf_serializers.CharField(),
        'refresh_token': drf_serializers.CharField(),
        'token_type': drf_serializers.CharField(),
        'expires_in': drf_serializers.IntegerField(),
        'refresh_expires_in': drf_serializers.IntegerField(),
    },
)


class LoginView(APIView):
    """
    Exchange credentials for an access token and a refresh token.

    POST ``{"username": <email>, "password": ...}``. Send ``access_token`` as
    ``Authorization: Bearer <access_token>``; when it expires, trade ``refresh_token`` for a new
    pair at ``/auth/token/refresh/``.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    @extend_schema(request=LoginUserSerializer, responses=TokenPairResponse)
    def post(self, request):
        serializer = LoginUserSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'detail': _('Invalid credentials')}, status=status.HTTP_400_BAD_REQUEST)
        return Response(refresh_tokens.issue_token_pair(serializer.validated_data['user']))


class RefreshView(APIView):
    """
    Trade a refresh token for a new access + refresh token.

    Refresh tokens are single use: the one you send is revoked and replaced. Sending a token that
    was already used revokes the whole session, so always store the newest ``refresh_token``.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'refresh'

    @extend_schema(request=RefreshTokenSerializer, responses=TokenPairResponse)
    def post(self, request):
        serializer = RefreshTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            _user, pair = refresh_tokens.rotate(serializer.validated_data['refresh_token'])
        except refresh_tokens.InvalidRefreshToken:
            response = Response({'detail': _('Invalid or expired refresh token')}, status=status.HTTP_401_UNAUTHORIZED)
            response['WWW-Authenticate'] = 'Bearer error="invalid_token"'
            return response
        return Response(pair)


class LogoutView(APIView):
    """Revoke the session a refresh token belongs to. Always returns 204, even for unknown tokens."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'refresh'

    @extend_schema(request=RefreshTokenSerializer, responses={204: None})
    def post(self, request):
        serializer = RefreshTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        refresh_tokens.logout(serializer.validated_data['refresh_token'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(APIView):
    """
    Start a password reset: email the account a link carrying the ``uidb64`` and ``token`` that
    ``/auth/password_reset/confirm/`` expects.

    The answer is the same whether or not the address has an account, so this cannot be used to find out
    who is registered. It is rate limited (``PASSWORD_RESET_THROTTLE_RATE``).
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_reset'

    @extend_schema(request=PasswordResetRequestSerializer, responses={200: None})
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=serializer.validated_data['email'], is_active=True).first()
        if user is not None:
            send_password_reset_email(user)
        return Response({'detail': _('If that address has an account, a reset link has been sent.')})


class PasswordResetConfirmView(APIView):
    """
    Confirm a password reset.

    Expects a POST with 'uidb64', 'token', 'new_password1' and 'new_password2'. The token is
    checked against the user identified by 'uidb64'; on success the password is replaced.
    """

    permission_classes = [AllowAny]

    @extend_schema(request=PasswordResetConfirmSerializer, responses={200: None})
    def post(self, request, *args, **kwargs):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        user = self.get_user(data['uidb64'])
        if user is None or not default_token_generator.check_token(user, data['token']):
            return Response({'detail': _('Invalid token')}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(data['new_password1'])
        user.save()
        refresh_tokens.revoke_all_for_user(user)  # a reset must end every existing session
        return Response({'detail': _('Password reset successful')}, status=status.HTTP_200_OK)

    @staticmethod
    def get_user(uidb64):
        """Return the user for a base64-encoded primary key, or None if it is invalid."""
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            return User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return None


class AcceptInvitationView(APIView):
    """
    View for accepting user invitations using a valid token.
    
    This view handles the logic for accepting a user invitation by validating the token,
    creating a new user (identified by the invited email), and associating them with the company specified in the invitation.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        request=inline_serializer(
            'AcceptInvitationRequest',
            {'token': drf_serializers.UUIDField(), 'password': drf_serializers.CharField()},
        ),
        responses={201: None},
    )
    def post(self, request):
        """
        Handle POST requests to accept an invitation.

        :param request: The incoming HTTP request
        :return: A JSON response indicating success or failure
        """
        token = request.data.get('token')
        
        if not token:
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            invitation = UserInvitation.objects.get(
                token=uuid.UUID(str(token)),
                expires_at__gt=timezone.now(),
                accepted_at__isnull=True,
            )
        except (UserInvitation.DoesNotExist, ValueError):
            return Response({'error': 'Invalid or expired token'}, status=status.HTTP_400_BAD_REQUEST)

        password = request.data.get('password')
        if not password:
            return Response({'error': 'Password is required'}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email__iexact=invitation.invited_email).exists():
            return Response({'error': 'A user with this email already exists'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user = User.objects.create_user(
                email=invitation.invited_email, password=password, company=invitation.company
            )
            user.groups.add(Group.objects.get_or_create(name=invitation.role)[0])
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=['accepted_at'])

        return Response({'message': 'Invitation accepted successfully'}, status=status.HTTP_201_CREATED)


class InvitationViewSet(
    TenantScopedMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Company staff invite new users by email; the link in the email leads to ``/auth/invitations/accept/``.

    Inviting an address again re-sends the invitation if the previous one expired, and is refused while one is
    still pending or once it has been accepted.
    """

    queryset = UserInvitation.objects.all()
    serializer_class = InvitationSerializer
    permission_classes = [IsAuthenticated, HasCompany, IsAdminUser]

    @extend_schema(request=InvitationCreateSerializer, responses={201: InvitationSerializer})
    def create(self, request, *args, **kwargs):
        if request.user.company_id is None:
            return Response({'detail': _('Your account is not linked to a company.')}, status=status.HTTP_400_BAD_REQUEST)
        data = InvitationCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        email, role = data.validated_data['invited_email'], data.validated_data['role']

        # One generic message: this must not reveal which addresses already have accounts.
        if User.objects.filter(email__iexact=email).exists():
            return Response({'invited_email': [_('This email address cannot be invited.')]}, status=status.HTTP_400_BAD_REQUEST)

        existing = UserInvitation.objects.filter(company=request.user.company, invited_email__iexact=email).first()
        if existing is not None and (existing.accepted_at or existing.expires_at > timezone.now()):
            return Response({'invited_email': [_('This email address cannot be invited.')]}, status=status.HTTP_400_BAD_REQUEST)

        fields = {'role': role, 'token': uuid.uuid4(), 'expires_at': timezone.now() + timedelta(hours=settings.INVITATION_TTL_HOURS)}
        if existing is None:
            invitation = UserInvitation.objects.create(company=request.user.company, invited_email=email, **fields)
        else:  # the earlier invitation expired: issue a fresh token
            for name, value in fields.items():
                setattr(existing, name, value)
            existing.save()
            invitation = existing

        body = InvitationSerializer(invitation).data
        body['email_sent'] = send_invitation_email(invitation)
        return Response(body, status=status.HTTP_201_CREATED)
