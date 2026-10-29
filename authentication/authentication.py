# authentication/authentication.py

import jwt
from django.contrib.auth import get_user_model
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import authentication, exceptions

from .jwt_handler import decode_token, password_stamp
from .role_access import role_may


class JWTAuthentication(authentication.BaseAuthentication):
    """
    ``Authorization: Bearer <token>`` authentication.

    The user (and therefore their company) is always loaded from the database, so deactivating a
    user or moving them to another company takes effect immediately.
    """

    keyword = 'Bearer'

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].decode().lower() != self.keyword.lower():
            return None  # not ours; let other authenticators try
        if len(header) != 2:
            raise exceptions.AuthenticationFailed('Invalid Authorization header.')

        try:
            payload = decode_token(header[1].decode())
        except (jwt.InvalidTokenError, UnicodeDecodeError):
            raise exceptions.AuthenticationFailed('Invalid or expired token.')

        try:
            user = get_user_model().objects.get(pk=payload['user_id'], is_active=True)
        except get_user_model().DoesNotExist:
            raise exceptions.AuthenticationFailed('User not found or inactive.')
        if payload['pw'] != password_stamp(user):
            raise exceptions.AuthenticationFailed('Password changed; please log in again.')
        if user.is_operator and not role_may(user.role, request.method, request.path):
            raise exceptions.PermissionDenied('Your role does not cover this.')
        return user, header[1].decode()

    def authenticate_header(self, request):
        return self.keyword


class PasswordOnlyBasicAuthentication(authentication.BasicAuthentication):
    """
    HTTP Basic (handy for scripts and the browsable API), but never for accounts that need a second factor:
    admins and anyone with two-factor sign-in on must sign in through ``/auth/login/``.
    """

    def authenticate_credentials(self, userid, password, request=None):
        from . import two_factor

        user, auth = super().authenticate_credentials(userid, password, request)
        if two_factor.is_required(user) or two_factor.is_enabled(user):
            raise exceptions.AuthenticationFailed('Two-factor sign-in applies to this account; use /auth/login/.')
        return user, auth


class JWTAuthenticationScheme(OpenApiAuthenticationExtension):
    """Documents the Bearer scheme in the OpenAPI schema."""

    target_class = 'authentication.authentication.JWTAuthentication'
    name = 'jwtAuth'

    def get_security_definition(self, auto_schema):
        return {'type': 'http', 'scheme': 'bearer', 'bearerFormat': 'JWT'}


class PasswordOnlyBasicAuthenticationScheme(OpenApiAuthenticationExtension):
    """Documents the restricted HTTP Basic scheme like DRF's own Basic."""

    target_class = 'authentication.authentication.PasswordOnlyBasicAuthentication'
    name = 'basicAuth'

    def get_security_definition(self, auto_schema):
        return {'type': 'http', 'scheme': 'basic'}
