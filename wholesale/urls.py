from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'customers', views.TradeCustomerViewSet, basename='tradecustomer')
router.register(r'groups', views.CustomerGroupViewSet, basename='customergroup')
router.register(r'price-lists', views.PriceListViewSet, basename='pricelist')
router.register(r'orders', views.SalesOrderViewSet, basename='salesorder')
router.register(r'invoices', views.InvoiceViewSet, basename='invoice')
router.register(r'runs', views.DeliveryRunViewSet, basename='deliveryrun')
router.register(r'returns', views.CustomerReturnViewSet, basename='customerreturn')
router.register(r'credit-notes', views.CreditNoteViewSet, basename='creditnote')
router.register(r'payments', views.PaymentViewSet, basename='payment')
router.register(r'allocations', views.AllocationViewSet, basename='allocation')
router.register(r'customer-emails', views.CustomerEmailViewSet, basename='customeremail')

urlpatterns = [
    path('prices/resolve/', views.PriceResolveView.as_view(), name='price-resolve'),
    *router.urls,
]
