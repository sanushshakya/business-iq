# sales/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'sessions', views.TillSessionViewSet, basename='tillsession')
router.register(r'sales', views.SaleViewSet, basename='sale')

urlpatterns = [
    path('', include(router.urls)),
]
