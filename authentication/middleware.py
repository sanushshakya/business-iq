"""
authentication/middleware.py

Middleware to read a JWT, extract company_id, and attach request.company to the request object.
"""

import jwt
from django.conf import settings
from django.http import JsonResponse

from tenants.models import Company

JWT_AUTH_HEADER_PREFIX = 'Bearer'
JWT_ALGORITHM = 'HS256'


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if auth_header and auth_header.startswith(JWT_AUTH_HEADER_PREFIX):
            try:
                token = auth_header.split()[1]
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
                company_id = payload.get('company_id')
                if not company_id:
                    return JsonResponse({"error": "Invalid token - company_id not found"}, status=401)
                request.company = Company.objects.get(id=company_id)
            except (jwt.InvalidTokenError, IndexError):
                # InvalidTokenError also covers ExpiredSignatureError
                return JsonResponse({"error": "Invalid or expired token"}, status=401)
            except Company.DoesNotExist:
                return JsonResponse({"error": "Company not found with provided ID"}, status=404)

        return self.get_response(request)
