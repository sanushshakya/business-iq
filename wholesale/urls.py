from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'customers', views.TradeCustomerViewSet, basename='tradecustomer')
router.register(r'groups', views.CustomerGroupViewSet, basename='customergroup')
router.register(r'price-lists', views.PriceListViewSet, basename='pricelist')

urlpatterns = [
    path('prices/resolve/', views.PriceResolveView.as_view(), name='price-resolve'),
    *router.urls,
]
