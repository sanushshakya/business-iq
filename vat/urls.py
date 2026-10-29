from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import VatRateViewSet

router = DefaultRouter()
router.register(r'rates', VatRateViewSet, basename='vat-rate')

urlpatterns = [path('', include(router.urls))]
