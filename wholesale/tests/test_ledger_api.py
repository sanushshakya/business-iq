"""G47: the customer ledger over the API — statement, adjustments, balance, roles and tenancy."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK
from wholesale.invoices import create_invoice, issue
from wholesale.models import LedgerEntry, TradeCustomer
from wholesale.tests.test_credit_notes import CreditBase

D = Decimal


class LedgerApiTests(CreditBase):
    def setUp(self):
        super().setUp()
        self.invoice = issue(create_invoice(self.picked_order(), self.staff), self.staff)  # 12.24
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))

    def ledger(self, client=None, customer=None, **params):
        return (client or self.api).get(reverse('tradecustomer-ledger', args=[(customer or self.customer).pk]),
                                        params)

    def adjust(self, body, client=None, customer=None):
        return (client or self.api).post(reverse('tradecustomer-adjustments', args=[(customer or self.customer).pk]),
                                         body, format='json')

    def test_statement_with_opening_running_and_closing(self):
        self.assertEqual(self.adjust({'amount': '-0.24', 'reason': 'Fake write-off'}).status_code, 201)
        data = self.ledger().json()
        self.assertEqual((data['opening'], data['closing']), ('0.00', '12.00'))
        self.assertEqual([(row['kind'], row['amount'], row['running']) for row in data['entries']],
                         [('invoice', '12.24', '12.24'), ('adjustment', '-0.24', '12.00')])
        self.assertEqual(data['entries'][0]['invoice'], self.invoice.pk)
        self.assertEqual(data['entries'][1]['reason'], 'Fake write-off')
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        later = self.ledger(**{'from': tomorrow, 'to': tomorrow}).json()
        self.assertEqual((later['opening'], later['entries'], later['closing']), ('12.00', [], '12.00'))
        customer = self.api.get(reverse('tradecustomer-detail', args=[self.customer.pk])).json()
        self.assertEqual(customer['balance'], '12.00')
        listed = self.api.get(reverse('tradecustomer-list')).json()['results']
        self.assertEqual([row['balance'] for row in listed], ['12.00'])

    def test_bad_ranges_are_refused(self):
        for params in ({'from': 'soon'}, {'from': self.today.isoformat(), 'to': (self.today - timedelta(days=1))
                                          .isoformat()},
                       {'from': (self.today - timedelta(days=367)).isoformat(), 'to': self.today.isoformat()}):
            with self.subTest(params=params):
                self.assertEqual(self.ledger(**params).status_code, 400)

    def test_adjustment_rules(self):
        for body in ({'amount': '0', 'reason': 'Fake'}, {'amount': '1.005', 'reason': 'Fake'},
                     {'amount': '1000000.00', 'reason': 'Fake'}, {'amount': '1.00', 'reason': ' '},
                     {'amount': '1.00'}, {'reason': 'Fake'}):
            with self.subTest(body=body):
                self.assertEqual(self.adjust(body).status_code, 400)
        made = self.adjust({'amount': '5.00', 'reason': 'Fake delivery charge'}).json()
        self.assertEqual((made['kind'], made['amount'], made['created_by']), ('adjustment', '5.00', self.staff.pk))
        self.assertEqual(LedgerEntry.objects.filter(customer=self.customer).count(), 2)

    def test_roles_and_no_edit_or_delete(self):
        self.assertEqual(self.ledger(self.member).status_code, 200)
        self.assertEqual(self.adjust({'amount': '1.00', 'reason': 'Fake'}, self.member).status_code, 403)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', STOCK)
        stock = APIClient()
        stock.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(self.ledger(stock).status_code, 403)
        url = reverse('tradecustomer-ledger', args=[self.customer.pk])
        for method in ('put', 'patch', 'delete', 'post'):
            self.assertEqual(getattr(self.api, method)(url, {}, format='json').status_code, 405)

    def test_tenancy(self):
        other = CompanyFactory()
        theirs = TradeCustomer.objects.create(company=other, account_code='C-1', legal_name='E2E Fake Other')
        self.assertEqual(self.ledger(customer=theirs).status_code, 404)
        self.assertEqual(self.adjust({'amount': '1.00', 'reason': 'Fake'}, customer=theirs).status_code, 404)
        self.assertFalse(LedgerEntry.objects.filter(customer=theirs).exists())
