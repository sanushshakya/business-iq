"""G49 over the API: payments, open items, allocating, releasing, reversing; roles and tenancy."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK
from wholesale.ledger import balance, post_adjustment
from wholesale.models import Allocation, LedgerEntry, Payment, TradeCustomer
from wholesale.tests.test_payments import PaymentCase

D = Decimal


class PaymentApiTests(PaymentCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))

    def record(self, client=None, **fields):
        body = {'customer': self.customer.pk, 'amount': '15.00', 'method': 'bank_transfer',
                'received_on': self.today.isoformat(), **fields}
        return (client or self.api).post(reverse('payment-list'), body, format='json')

    def items(self, client=None, customer=None):
        return (client or self.api).get(reverse('tradecustomer-open-items', args=[(customer or self.customer).pk]))

    def test_record_oldest_first_and_read_it_back(self):
        made = self.record(reference='Fake BACS 1')
        self.assertEqual(made.status_code, 201)
        data = made.json()
        self.assertEqual([(row['number'], row['amount']) for row in data['allocations']],
                         [('INV-000001', '12.24'), ('INV-000002', '2.76')])
        self.assertEqual((data['allocated'], data['on_account']), ('15.00', '0.00'))
        items = self.items().json()
        self.assertEqual([(row['number'], row['open']) for row in items['debits']], [('INV-000002', '5.48')])
        self.assertEqual(items['credits'], [])
        listed = self.member.get(reverse('payment-list'), {'customer': self.customer.pk}).json()
        self.assertEqual([row['id'] for row in listed['results']], [data['id']])
        self.assertEqual(balance(self.customer), D('5.48'))

    def test_manual_on_account_and_later_allocation(self):
        second = LedgerEntry.objects.get(invoice=self.second).pk
        manual = self.record(amount='5.00', allocate='manual', allocations=[{'debit': second, 'amount': '5.00'}])
        self.assertEqual(manual.status_code, 201)
        kept = self.record(amount='4.00', allocate='none').json()
        self.assertEqual(kept['on_account'], '4.00')
        credit_entry = [row for row in self.items().json()['credits']][0]
        url = reverse('tradecustomer-allocations', args=[self.customer.pk])
        self.assertEqual(self.api.post(url, {'credit': credit_entry['entry'], 'allocate': 'manual', 'allocations': [
            {'debit': second, 'amount': '4.00'}]}, format='json').status_code, 400)  # only 3.24 open
        done = self.api.post(url, {'credit': credit_entry['entry'], 'allocate': 'oldest'}, format='json')
        self.assertEqual(done.status_code, 200)
        self.assertEqual([(row['number'], row['open']) for row in done.json()['debits']],
                         [('INV-000001', '8.24'), ('INV-000002', '3.24')])

    def test_release_and_reverse(self):
        payment = self.record().json()
        allocation = payment['allocations'][1]['id']
        released = self.api.post(reverse('allocation-release', args=[allocation]))
        self.assertEqual(released.status_code, 200)
        self.assertIsNotNone(released.json()['released_at'])
        self.assertEqual(self.api.post(reverse('allocation-release', args=[allocation])).status_code, 400)
        url = reverse('payment-reverse', args=[payment['id']])
        self.assertEqual(self.api.post(url, {}, format='json').status_code, 400)
        reversed_payment = self.api.post(url, {'reason': 'Fake: cheque bounced'}, format='json').json()
        self.assertEqual((reversed_payment['reversal_reason'], reversed_payment['on_account']),
                         ('Fake: cheque bounced', '0.00'))
        self.assertEqual(self.api.post(url, {'reason': 'Fake'}, format='json').status_code, 400)
        self.assertEqual(balance(self.customer), D('20.48'))

    def test_bad_input_and_a_retry(self):
        for fields in ({'amount': '0'}, {'amount': '1.001'}, {'method': 'bitcoin'}, {'allocate': 'manual'},
                       {'received_on': '2999-01-01'}):
            with self.subTest(fields=fields):
                self.assertEqual(self.record(**fields).status_code, 400)
        body = {'client_ref': '1f0e9a0c-7f4b-4a77-9d0e-9e5d2b7a3c11', 'amount': '2.00'}
        self.assertEqual((self.record(**body).status_code, self.record(**body).status_code), (201, 200))
        self.assertEqual(self.record(**{**body, 'amount': '3.00'}).status_code, 409)
        self.assertEqual(Payment.objects.count(), 1)

    def test_roles_and_no_edit_or_delete(self):
        payment = self.record().json()
        self.assertEqual(self.record(self.member).status_code, 403)
        self.assertEqual(self.member.get(reverse('payment-detail', args=[payment['id']])).status_code, 200)
        self.assertEqual(self.items(self.member).status_code, 200)
        self.assertEqual(self.member.post(reverse('payment-reverse', args=[payment['id']]), {'reason': 'Fake'},
                                          format='json').status_code, 403)
        url = reverse('payment-detail', args=[payment['id']])
        for method in ('put', 'patch', 'delete'):
            self.assertEqual(getattr(self.api, method)(url, {}, format='json').status_code, 405)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        picker = APIClient()
        picker.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(picker.get(reverse('payment-list')).status_code, 403)
        self.assertEqual(self.items(picker).status_code, 403)

    def test_tenancy(self):
        other = CompanyFactory()
        theirs = TradeCustomer.objects.create(company=other, account_code='C-9', legal_name='E2E Fake Other')
        owed = post_adjustment(theirs, D('3.00'), 'Fake', UserFactory(company=other, is_staff=True))
        self.assertEqual(self.record(customer=theirs.pk).status_code, 400)
        self.assertEqual(self.items(customer=theirs).status_code, 404)
        mine = LedgerEntry.objects.get(invoice=self.first).pk
        self.assertEqual(self.record(allocate='manual', allocations=[{'debit': owed.pk, 'amount': '1.00'}])
                         .status_code, 400)
        admin = APIClient()
        admin.force_authenticate(UserFactory(company=self.company, is_staff=True, is_superuser=True))
        self.assertEqual(self.record(admin, customer=theirs.pk).status_code, 400)
        self.assertEqual(self.items(admin, customer=theirs).status_code, 404)
        self.assertEqual(admin.post(reverse('tradecustomer-allocations', args=[theirs.pk]),
                                    {'credit': mine, 'allocate': 'oldest'}, format='json').status_code, 404)
        self.assertFalse(Payment.objects.filter(customer=theirs).exists())
        self.assertFalse(Allocation.objects.filter(debit=owed).exists())
