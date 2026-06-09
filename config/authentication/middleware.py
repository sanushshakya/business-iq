"""
config/authentication/middleware.py

Middleware to read JWT, extract company_id, and attach request.company to the request object.
"""

import jwt
from django.http import JsonResponse
from rest_framework_jwt.settings import api_settings
from .models import Company

JWT_AUTH_HEADER_PREFIX = 'Bearer'

class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if the JWT token is present in the Authorization header
        auth_header = request.META.get('HTTP_AUTHORIZATION', None)
        if auth_header and auth_header.startswith(JWT_AUTH_HEADER_PREFIX):
            try:
                # Extract the token from the header
                token = auth_header.split()[1]
                # Decode the JWT token to get payload
                payload = jwt.decode(token, api_settings.JWT_SECRET_KEY, algorithms=[api_settings.JWT_ALGORITHM])
                # Extract company_id from the payload
                company_id = payload.get('company_id')
                if company_id:
                    # Attach the company to the request object
                    request.company = Company.objects.get(id=company_id)
                else:
                    raise ValueError("Invalid JWT token - company_id not found")
            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                return JsonResponse({"error": "Invalid or expired token"}, status=401)
            except Company.DoesNotExist:
                return JsonResponse({"error": "Company not found with provided ID"}, status=404)

        response = self.get_response(request)
        return response