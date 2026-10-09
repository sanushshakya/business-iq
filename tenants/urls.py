# tenants/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'branches', views.BranchViewSet, basename='branch')
router.register(r'tills', views.TillViewSet, basename='till')

urlpatterns = [
    path('company/', views.MyCompanyView.as_view(), name='my-company'),
    path('', include(router.urls)),
]
