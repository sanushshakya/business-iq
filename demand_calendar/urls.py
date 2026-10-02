# demand_calendar/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'events', views.EventViewSet, basename='event')
router.register(r'alerts', views.DemandAlertViewSet, basename='demandalert')

urlpatterns = [
    path('upcoming/', views.get_next_three_months_events, name='get_next_three_months_events'),
    path('', include(router.urls)),
]
