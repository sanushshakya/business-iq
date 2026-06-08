"""
config/inventory/urls.py

URL routing for the inventory app in the iq project.
"""

from django.urls import path
from .views import InventoryListView, InventoryDetailView, InventoryCreateView, InventoryUpdateView, InventoryDeleteView

app_name = 'inventory'

urlpatterns = [
    path('', InventoryListView.as_view(), name='list'),
    path('<int:pk>/', InventoryDetailView.as_view(), name='detail'),
    path('new/', InventoryCreateView.as_view(), name='create'),
    path('<int:pk>/edit/', InventoryUpdateView.as_view(), name='update'),
    path('<int:pk>/delete/', InventoryDeleteView.as_view(), name='delete'),
]