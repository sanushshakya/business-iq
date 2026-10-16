# sync/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'tasks', views.SyncTaskViewSet, basename='synctask')
router.register(r'shopify-connections', views.ShopifyConnectionViewSet, basename='shopifyconnection')

urlpatterns = [
    path('inbound/storefront/<int:connection_id>/', views.StorefrontInboundView.as_view(), name='storefront-inbound'),
    path('', include(router.urls)),
]
