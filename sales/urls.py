# sales/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'sessions', views.TillSessionViewSet, basename='tillsession')
router.register(r'sales', views.SaleViewSet, basename='sale')
router.register(r'exceptions', views.SaleExceptionViewSet, basename='saleexception')

urlpatterns = [
    path('catalogue/', views.CatalogueView.as_view(), name='till-catalogue'),
    path('offline-sales/', views.OfflineSalesView.as_view(), name='offline-sales'),
    path('', include(router.urls)),
]
