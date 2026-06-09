from django.urls import path

# Import views from the sync app
from .views import SyncView, AnotherSyncView

urlpatterns = [
    # Define URL patterns for the sync app
    path('sync/', SyncView.as_view(), name='sync'),
    path('another-sync/', AnotherSyncView.as_view(), name='another_sync'),
]