# common/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    DemandAlertCreateAPIView,
    DemandAlertDismissAPIView,
    SettingViewSet,
    StockAlertListAPIView,
    VerifyEmailTokenView,
)

router = DefaultRouter()
router.register(r'api/settings', SettingViewSet, basename='setting')

urlpatterns = [
    path('', include(router.urls)),
    path('api/alerts/stock/', StockAlertListAPIView.as_view(), name='stock-alert-list'),
    path('api/alerts/demand/create/', DemandAlertCreateAPIView.as_view(), name='demand-alert-create'),
    path('api/alerts/demand/dismiss/<int:pk>/', DemandAlertDismissAPIView.as_view(), name='demand-alert-dismiss'),
    path('api/auth/verify-email-token/', VerifyEmailTokenView.as_view(), name='verify-email-token'),
]
