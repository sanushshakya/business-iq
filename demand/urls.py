# demand/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'demands', views.DemandViewSet, basename='demand')
router.register(r'events', views.CulturalEventViewSet, basename='culturalevent')
router.register(r'keywords', views.EventProductKeywordViewSet, basename='eventproductkeyword')

urlpatterns = [
    path('', include(router.urls)),
]
