from django.urls import path
from .views import PasswordResetConfirmView

# Module docstring
"""
URL patterns for handling password reset confirmations.

This module defines the URL patterns for the password reset confirmation feature within a Django application.
"""

urlpatterns = [
    # URL pattern for the password reset confirmation view
    path('password_reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]