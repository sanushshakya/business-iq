"""
config/common/urls.py

This file contains the URL configurations for common parts of the iq project,
which are not specific to any single app but rather shared across multiple apps.
"""

from django.urls import path  # Removed re_path as it's not used
from .views import StockAlertListAPIView, DemandAlertCreateAPIView, DemandAlertDismissAPIView, VerifyEmailTokenView

urlpatterns = [
    # Existing URL patterns can be kept here if necessary
    # For example:
    # path('existing-endpoint/', views.existing_view, name='existing_view'),
    
    # Add the URL pattern for the stock alerts API
    path('api/alerts/stock/', StockAlertListAPIView.as_view(), name='stock-alert-list'),
    
    # New URL patterns for demand alerts
    path('api/alerts/demand/create/', DemandAlertCreateAPIView.as_view(), name='demand-alert-create'),
    path('api/alerts/demand/dismiss/<int:pk>/', DemandAlertDismissAPIView.as_view(), name='demand-alert-dismiss'),

    # New URL pattern for email token verification
    path('api/auth/verify-email-token/', VerifyEmailTokenView.as_view(), name='verify-email-token'),

    # WebSocket consumer URL pattern (removed as it's not used)
    # re_path(r'ws/socket-server/$', consumers.MyWebSocketConsumer.as_view()),
]