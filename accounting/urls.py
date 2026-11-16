from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views
from .hmrc import views as hmrc

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
    path('hmrc/', hmrc.HmrcStatusView.as_view(), name='hmrc-status'),
    path('hmrc/connect/', hmrc.HmrcConnectView.as_view(), name='hmrc-connect'),
    path('hmrc/callback/', hmrc.HmrcCallbackView.as_view(), name='hmrc-callback'),
    path('hmrc/connection/', hmrc.HmrcConnectionView.as_view(), name='hmrc-connection'),
    path('hmrc/obligations/', hmrc.HmrcObligationsView.as_view(), name='hmrc-obligations'),
    path('hmrc/fraud-check/', hmrc.HmrcFraudCheckView.as_view(), name='hmrc-fraud-check'),
    path('hmrc/returns/', hmrc.HmrcReturnsView.as_view(), name='hmrc-returns'),
    path('hmrc/returns/<int:pk>/check/', hmrc.HmrcReturnCheckView.as_view(), name='hmrc-return-check'),
    *router.urls,
]
