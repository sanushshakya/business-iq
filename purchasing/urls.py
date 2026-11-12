from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'supplier-products', views.SupplierProductViewSet, basename='supplierproduct')
router.register(r'orders', views.PurchaseOrderViewSet, basename='purchaseorder')

urlpatterns = router.urls
