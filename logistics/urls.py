# config/logistics/urls.py

from django.urls import path
from .views import UserSupplierListView, UserSupplierDetailView, AlternativeSupplierReadOnlyView, FreightAlertCreateView  # Import your views here

urlpatterns = [
    # Define URL patterns for logistics app
    path('', LogisticsView.as_view(), name='logistics'),  # Example URL pattern

    # URLs for UserSupplier
    path('usersuppliers/', UserSupplierListView.as_view(), name='user-supplier-list'),
    path('usersuppliers/<int:pk>/', UserSupplierDetailView.as_view(), name='user-supplier-detail'),

    # Read-only URL for AlternativeSupplier
    path('alternativesuppliers/<int:pk>/', AlternativeSupplierReadOnlyView.as_view(), name='alternative-supplier-read-only'),

    # URL for FreightAlert
    path('freightalerts/', FreightAlertCreateView.as_view(), name='freight-alert-create'),
]