from django.urls import path
from .views import PasswordResetConfirmView

"""
Module docstring
==================================================
authentication/urls.py

This module defines the URL patterns for handling password reset confirmations within a Django application.
"""

urlpatterns = [
    """
    URL pattern for the password reset confirmation view.

    This section includes the route for confirming a password reset request sent via email.
    """

    # URL pattern for the password reset confirmation view
    path('password_reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]