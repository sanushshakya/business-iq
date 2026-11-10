"""G54 over the API: profit and loss, balance sheet, the VAT return and filing it; roles and tenancy."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from accounting.models import Account, VatReturn
from accounting.tests.test_vat_return import VatReturnCase
from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK

D = Decimal


class ReportsApiTests(VatReturnCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.start = (self.today.replace(day=1) - timedelta(days=1)).replace(day=1)
        self.end = self.today.replace(day=1) - timedelta(days=1)
        self.bill(D('10.00'), D('2.00'), day=self.start + timedelta(days=2))
        self.range = {'from': self.start.isoformat(), 'to': self.end.isoformat()}

    def test_profit_and_loss_and_balance_sheet(self):
        pl = self.member.get(reverse('profit-and-loss'), self.range).json()
        self.assertEqual((pl['total_expenses'], pl['net_profit']), ('10.00', '-10.00'))
        self.assertEqual(pl['expenses'], [{'account': self.account('5000').pk, 'code': '5000', 'name': 'Purchases',
                                           'amount': '10.00'}])
        sheet = self.member.get(reverse('balance-sheet'), {'on': self.end.isoformat()}).json()
        # The bill owes 12.00 to creditors less 2.00 VAT to reclaim (VAT on purchases is a liability account).
        self.assertEqual((sheet['total_assets'], sheet['total_liabilities'], sheet['total_liabilities_and_equity']),
                         ('0.00', '10.00', '0.00'))
        self.assertEqual(sheet['equity'][-1], {'account': None, 'code': None, 'name': 'Profit to date',
                                               'amount': '-10.00'})
        for url, params in ((reverse('profit-and-loss'), {'from': 'x'}), (reverse('balance-sheet'), {'on': '2026-1-1'}),
                            (reverse('profit-and-loss'), {'from': self.end.isoformat(), 'to': self.start.isoformat()})):
            self.assertEqual(self.member.get(url, params).status_code, 400)
        self.assertEqual(self.member.get(reverse('balance-sheet')).status_code, 200)  # default: today

    def test_vat_return_view_then_file(self):
        shown = self.member.get(reverse('vat-return'), self.range).json()
        self.assertEqual((shown['box4'], shown['box5'], shown['net_direction'], shown['box7'], shown['filed']),
                         ('2.00', '2.00', 'reclaim', '10', None))
        self.assertEqual(self.member.post(reverse('vatreturn-list'), self.range, format='json').status_code, 403)
        filed = self.api.post(reverse('vatreturn-list'), self.range, format='json')
        self.assertEqual(filed.status_code, 201)
        self.assertEqual((filed.json()['box4'], filed.json()['period_start']), ('2.00', self.start.isoformat()))
        again = self.member.get(reverse('vat-return'), self.range).json()
        self.assertEqual(again['filed']['id'], filed.json()['id'])
        self.assertEqual(self.api.post(reverse('vatreturn-list'), self.range, format='json').status_code, 400)
        detail = reverse('vatreturn-detail', args=[filed.json()['id']])
        for method in ('put', 'patch', 'delete'):
            self.assertEqual(getattr(self.api, method)(detail, {}, format='json').status_code, 405)
        listed = self.member.get(reverse('vatreturn-list')).json()['results']
        self.assertEqual([row['id'] for row in listed], [filed.json()['id']])
        self.assertEqual(self.api.post(reverse('vatreturn-list'), {'from': '1999-01-01', 'to': '1999-03-31'},
                                       format='json').status_code, 400)
        self.assertEqual(self.api.post(reverse('vatreturn-list'), {'from': self.today.replace(day=1).isoformat(),
                                                                   'to': self.today.isoformat()},
                                       format='json').status_code, 400)

    def test_account_vat_flag_is_editable(self):
        url = reverse('account-detail', args=[self.account('7500').pk])
        self.assertEqual(self.api.get(url).json()['vat_purchases'], False)
        self.assertEqual(self.api.patch(url, {'vat_purchases': True}, format='json').status_code, 200)
        self.assertTrue(Account.objects.get(pk=self.account('7500').pk).vat_purchases)

    def test_other_companies_and_operators(self):
        self.api.post(reverse('vatreturn-list'), self.range, format='json')
        stranger = APIClient()
        stranger.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True, is_superuser=True))
        self.assertEqual(stranger.get(reverse('vatreturn-list')).json()['results'], [])
        self.assertEqual(stranger.get(reverse('vat-return'), self.range).json()['box4'], '0.00')
        self.assertEqual(stranger.get(reverse('profit-and-loss'), self.range).json()['expenses'], [])
        self.assertEqual(VatReturn.objects.count(), 1)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        picker = APIClient()
        picker.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        for url in (reverse('profit-and-loss'), reverse('balance-sheet'), reverse('vat-return'),
                    reverse('vatreturn-list')):
            self.assertEqual(picker.get(url, self.range).status_code, 403)
