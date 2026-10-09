# logistics/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'freight-alerts', views.FreightAlertViewSet, basename='freightalert')
router.register(r'providers', views.LogisticProviderViewSet, basename='logisticprovider')
router.register(r'deliveries', views.DeliveryViewSet, basename='delivery')
router.register(r'suppliers', views.UserSupplierViewSet, basename='usersupplier')
router.register(r'alternative-suppliers', views.AlternativeSupplierViewSet, basename='alternativesupplier')
router.register(r'freight-rates', views.FreightRateViewSet, basename='freightrate')

urlpatterns = [
    path('', include(router.urls)),
]
