# sync/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'tasks', views.SyncTaskViewSet, basename='synctask')
router.register(r'shopify-connections', views.ShopifyConnectionViewSet, basename='shopifyconnection')

urlpatterns = [
    path('', include(router.urls)),
]
