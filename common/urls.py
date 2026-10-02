# common/urls.py

from django.urls import path

from .views import DemandAlertCreateAPIView, DemandAlertDismissAPIView, StockAlertListAPIView, VerifyEmailTokenView

urlpatterns = [
    path('api/alerts/stock/', StockAlertListAPIView.as_view(), name='stock-alert-list'),
    path('api/alerts/demand/create/', DemandAlertCreateAPIView.as_view(), name='demand-alert-create'),
    path('api/alerts/demand/dismiss/<int:pk>/', DemandAlertDismissAPIView.as_view(), name='demand-alert-dismiss'),
    path('api/auth/verify-email-token/', VerifyEmailTokenView.as_view(), name='verify-email-token'),
]
