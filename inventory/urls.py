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
router.register(r'locations', views.LocationViewSet, basename='location')
router.register(r'barcode-rules', views.BarcodeRuleViewSet, basename='barcoderule')
router.register(r'transfers', views.TransferViewSet, basename='transfer')
router.register(r'requisitions', views.RequisitionViewSet, basename='requisition')
router.register(r'yield-templates', views.YieldTemplateViewSet, basename='yieldtemplate')

urlpatterns = [
    path('', include(router.urls)),
]
