"""
auth/dependencies.py

This file contains custom dependency functions for handling authentication and authorization in Django REST Framework (DRF).
"""

import jwt
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed

def get_current_user(request):
    """
    Decode JWT token from request headers and return the current user.

    Args:
        request (HttpRequest): The incoming HTTP request object.

    Returns:
        User: The authenticated user object.

    Raises:
        AuthenticationFailed: If no valid token is provided or the token is invalid.
    """
    auth_header = request.headers.get('Authorization')
    
    if not auth_header:
        raise AuthenticationFailed('No token provided')

    try:
        token = auth_header.split()[1]
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=['HS256'])
        user_id = payload['user_id']
        # Assuming User model is available and correctly imported
        from authentication.models import User
        user = User.objects.get(pk=user_id)
        return user
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        raise AuthenticationFailed('Invalid or expired token')