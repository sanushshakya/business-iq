# pricing/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'plans', views.PricingPlanViewSet, basename='pricingplan')
router.register(r'subscriptions', views.SubscriptionViewSet, basename='subscription')
router.register(r'invoices', views.SupplierInvoiceViewSet, basename='supplierinvoice')
router.register(r'invoice-items', views.InvoiceLineItemViewSet, basename='invoicelineitem')
router.register(r'price-changes', views.PriceChangeLogViewSet, basename='pricechangelog')

urlpatterns = [
    path('recommendation/', views.PriceRecommendationView.as_view(), name='price-recommendation'),
    path('', include(router.urls)),
]
