from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'accounts', views.AccountViewSet, basename='account')
router.register(r'journals', views.JournalViewSet, basename='journal')
router.register(r'periods', views.PeriodViewSet, basename='period')

urlpatterns = [
    path('postings/run/', views.PostingRunView.as_view(), name='posting-run'),
    path('trial-balance/', views.TrialBalanceView.as_view(), name='trial-balance'),
    *router.urls,
]
