"""G48 over the API: the block answer, overrides, the customer's credit, roles and tenancy."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK
from wholesale.models import CreditOverride, SalesOrder, TradeCustomer
from wholesale.orders import confirm
from wholesale.tests.test_credit import CreditCase

D = Decimal


class CreditApiTests(CreditCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        self.picker = APIClient()
        self.picker.credentials(
            HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")

    def post(self, order, name, body=None, client=None):
        return (client or self.api).post(reverse(f'salesorder-{name}', args=[order.pk]), body or {}, format='json')

    def test_a_blocked_confirm_says_why_and_staff_override_it(self):
        self.limit(D('1.00'))
        order = self.draft(rice=2)
        refused = self.post(order, 'confirm')
        self.assertEqual(refused.status_code, 400)
        body = refused.json()
        self.assertEqual((body['code'], [reason['code'] for reason in body['reasons']]),
                         ('credit_blocked', ['over_limit']))
        self.assertIn('credit limit', body['detail'])
        self.assertEqual(self.post(order, 'confirm', {'credit_override_reason': 'Fake'}, self.member).status_code,
                         403)
        done = self.post(order, 'confirm', {'credit_override_reason': 'Fake: owner agreed'})
        self.assertEqual(done.status_code, 200)
        data = done.json()
        self.assertEqual((data['status'], data['credit_blocks']), ('confirmed', []))
        self.assertEqual((data['credit_override']['reason'], data['credit_override']['created_by'],
                          data['credit_override']['blocks']), ('Fake: owner agreed', self.staff.pk, ['over_limit']))

    def test_picking_is_blocked_with_the_reason_and_overridden_by_staff_only(self):
        order = confirm(self.draft(rice=3), self.staff)
        self.limit(D('1.00'))
        refused = self.post(order, 'start-pick', client=self.picker)
        self.assertEqual((refused.status_code, refused.json()['code']), (400, 'credit_blocked'))
        detail = self.api.get(reverse('salesorder-detail', args=[order.pk])).json()
        self.assertEqual([reason['code'] for reason in detail['credit_blocks']], ['over_limit'])
        listed = self.api.get(reverse('salesorder-list')).json()['results']
        self.assertEqual([row['credit_blocks'] for row in listed], [[]])
        pdf = self.picker.get(reverse('salesorder-pick-list', args=[order.pk]))
        self.assertEqual((pdf.status_code, pdf.json()['code']), (400, 'credit_blocked'))
        self.assertEqual(self.post(order, 'credit-override', {'reason': 'Fake'}, self.picker).status_code, 403)
        self.assertEqual(self.post(order, 'credit-override', {'reason': 'Fake'}, self.member).status_code, 403)
        self.assertEqual(self.post(order, 'credit-override', {}).status_code, 400)
        made = self.post(order, 'credit-override', {'reason': 'Fake: paid by phone'})
        self.assertEqual((made.status_code, made.json()['credit_override']['reason']), (200, 'Fake: paid by phone'))
        self.assertEqual(self.picker.get(reverse('salesorder-pick-list', args=[order.pk])).status_code, 200)
        self.assertEqual(self.post(order, 'start-pick', client=self.picker).status_code, 200)

    def test_override_refused_when_nothing_blocks_or_the_order_is_past_picking(self):
        order = confirm(self.draft(), self.staff)
        self.assertEqual(self.post(order, 'credit-override', {'reason': 'Fake'}).status_code, 400)
        picked = self.picked_order()
        self.limit(D('1.00'))
        self.assertEqual(self.post(picked, 'credit-override', {'reason': 'Fake'}).status_code, 400)
        self.assertEqual(self.post(self.draft(), 'credit-override', {'reason': 'Fake'}).status_code, 400)
        self.assertFalse(CreditOverride.objects.exists())

    def test_the_customer_credit_and_its_fields(self):
        url = reverse('tradecustomer-detail', args=[self.customer.pk])
        changed = self.api.patch(url, {'credit_limit': '500.00', 'status': 'on_hold',
                                       'hold_reason': 'Fake: cheque bounced'}, format='json')
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.api.patch(url, {'credit_limit': '-1.00'}, format='json').status_code, 400)
        report = self.member.get(reverse('tradecustomer-credit', args=[self.customer.pk])).json()
        self.assertEqual({key: report[key] for key in ('balance', 'open_orders', 'exposure', 'credit_limit',
                                                        'available', 'overdue_days')},
                         {'balance': '0.00', 'open_orders': '0.00', 'exposure': '0.00', 'credit_limit': '500.00',
                          'available': '500.00', 'overdue_days': 7})
        self.assertEqual([reason['code'] for reason in report['blocks']], ['on_hold'])
        self.assertIn('Fake: cheque bounced', report['blocks'][0]['message'])
        self.assertEqual(self.picker.get(reverse('tradecustomer-credit', args=[self.customer.pk])).status_code, 403)

    def test_tenancy(self):
        other = CompanyFactory()
        theirs = TradeCustomer.objects.create(company=other, account_code='C-9', legal_name='E2E Fake Other')
        order = SalesOrder.objects.create(company=other, number=1, customer=theirs, delivery_date=self.today,
                                          line1='1 Fake Street', town='Testtown', postcode='TT1 1TT')
        self.assertEqual(self.api.get(reverse('tradecustomer-credit', args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.post(order, 'credit-override', {'reason': 'Fake'}).status_code, 404)
        admin = APIClient()
        admin.force_authenticate(UserFactory(company=self.company, is_staff=True, is_superuser=True))
        self.assertEqual(admin.get(reverse('tradecustomer-credit', args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.post(order, 'credit-override', {'reason': 'Fake'}, admin).status_code, 404)
        self.assertEqual(self.post(order, 'confirm', {'credit_override_reason': 'Fake'}, admin).status_code, 404)
        self.assertFalse(CreditOverride.objects.exists())

    def test_a_finished_pick_list_can_be_printed_again(self):
        picked = self.picked_order()
        self.limit(D('1.00'))
        self.assertEqual(self.picker.get(reverse('salesorder-pick-list', args=[picked.pk])).status_code, 200)

    def test_pickers_are_told_to_ask_the_office_without_the_figures(self):
        TradeCustomer.objects.filter(pk=self.customer.pk).update(hold_reason='Fake: cheque bounced')
        order = confirm(self.draft(rice=3), self.staff)
        self.limit(D('1.00'))
        refused = self.post(order, 'start-pick', client=self.picker).json()
        self.assertEqual(refused['reasons'], [{'code': 'over_limit', 'message': 'Credit hold: ask the office.'}])
        self.assertEqual(refused['detail'], 'Credit hold: ask the office.')
        self.post(order, 'credit-override', {'reason': 'Fake: paid by phone'})
        self.limit(D('0.50'))
        TradeCustomer.objects.filter(pk=self.customer.pk).update(status='on_hold')
        seen = self.picker.get(reverse('salesorder-detail', args=[order.pk])).json()
        self.assertEqual(seen['credit_blocks'], [{'code': 'on_hold', 'message': 'Credit hold: ask the office.'}])
        self.assertEqual(seen['credit_override'], None)
        self.assertNotIn('cheque', str(seen))
        self.assertEqual(self.post(order, 'confirm', {'credit_override_reason': 'Fake'}, self.picker).status_code,
                         403)

    def test_order_detail_queries_do_not_grow_with_the_ledger(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from wholesale.ledger import post_adjustment

        order = confirm(self.draft(), self.staff)
        url = reverse('salesorder-detail', args=[order.pk])
        with CaptureQueriesContext(connection) as before:
            self.api.get(url)
        for amount in ('5.00', '-1.00', '3.00', '-2.00', '4.00'):
            post_adjustment(self.customer, D(amount), 'Fake', self.staff)
        with CaptureQueriesContext(connection) as after:
            self.api.get(url)
        self.assertEqual(len(after), len(before))
        self.assertLessEqual(len(after), 16)
