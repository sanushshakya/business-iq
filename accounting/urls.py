from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'accounts', views.AccountViewSet, basename='account')
router.register(r'journals', views.JournalViewSet, basename='journal')
router.register(r'periods', views.PeriodViewSet, basename='period')
router.register(r'vat-returns', views.VatReturnViewSet, basename='vatreturn')

urlpatterns = [
    path('postings/run/', views.PostingRunView.as_view(), name='posting-run'),
    path('trial-balance/', views.TrialBalanceView.as_view(), name='trial-balance'),
    path('reports/profit-and-loss/', views.ProfitAndLossView.as_view(), name='profit-and-loss'),
    path('reports/balance-sheet/', views.BalanceSheetView.as_view(), name='balance-sheet'),
    path('vat-return/', views.VatReturnView.as_view(), name='vat-return'),
    path('exports/journals.csv', views.JournalExportView.as_view(), name='export-journals'),
    path('exports/vat.csv', views.VatExportView.as_view(), name='export-vat'),
    *router.urls,
]
