# inventory/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'categories', views.ProductCategoryViewSet, basename='category')
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'orders', views.OrderViewSet, basename='order')
router.register(r'suppliers', views.SupplierViewSet, basename='supplier')
router.register(r'batches', views.StockBatchViewSet, basename='stockbatch')
router.register(r'movements', views.StockMovementViewSet, basename='stockmovement')

urlpatterns = [
    path('', include(router.urls)),
]
