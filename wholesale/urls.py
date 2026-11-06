from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'customers', views.TradeCustomerViewSet, basename='tradecustomer')

urlpatterns = router.urls
