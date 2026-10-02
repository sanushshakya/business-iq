# logistics/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'freight-alerts', views.FreightAlertViewSet, basename='freightalert')
router.register(r'providers', views.LogisticProviderViewSet, basename='logisticprovider')
router.register(r'deliveries', views.DeliveryViewSet, basename='delivery')

urlpatterns = [
    path('', include(router.urls)),
]
