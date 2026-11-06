"""G40: sales orders over the API and in the admin. All names and addresses are fake."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from inventory.models import Product
from tenants.roles import STOCK, TILL
from wholesale.models import SalesOrder, TradeCustomer, TradeCustomerAddress

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.staff_user = UserFactory(company=self.company, is_staff=True)
        self.staff = APIClient()
        self.staff.force_authenticate(self.staff_user)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                     legal_name='E2E Fake Bistro Ltd', trading_name='Fake Bistro',
                                                     delivery_days=['fri'])
        self.default = TradeCustomerAddress.objects.create(customer=self.customer, label='Kitchen door',
                                                           line1='1 Fake Street', town='Testtown',
                                                           postcode='TT1 1TT', is_default=True)
        self.second = TradeCustomerAddress.objects.create(customer=self.customer, label='Second site',
                                                          line1='2 Fake Road', town='Testtown', postcode='TT2 2TT')
        self.each = ProductFactory(company=self.company, wholesale_price=D('2.5000'))
        self.loose = ProductFactory(company=self.company, sold_by=Product.WEIGHT, price_per_kg=D('12.99'),
                                    wholesale_price=D('8.0000'))
        self.catch = ProductFactory(company=self.company, sold_by=Product.WEIGHT, is_catch_weight=True,
                                    price_per_kg=D('9.99'), nominal_weight_kg=D('1.500'),
                                    wholesale_price=D('6.0000'))
        self.other_company = CompanyFactory()
        self.foreign_customer = TradeCustomer.objects.create(company=self.other_company, account_code='C-1',
                                                             legal_name='E2E Fake Theirs Ltd')
        self.foreign_address = TradeCustomerAddress.objects.create(customer=self.foreign_customer, label='Theirs',
                                                                   line1='9 Fake Lane', town='Elsewhere',
                                                                   postcode='TT9 9TT', is_default=True)
        self.foreign_product = ProductFactory(company=self.other_company)
        self.list_url = reverse('salesorder-list')

    def body(self, **fields):
        return {
            'customer': self.customer.pk, 'delivery_date': '2026-11-13', 'customer_ref': 'PO-77',
            'lines': [
                {'product': self.each.pk, 'quantity': 4},
                {'product': self.loose.pk, 'weight_kg': '2.500'},
                {'product': self.catch.pk, 'quantity': 2, 'note': 'Large ones'},
            ],
            **fields,
        }

    def create(self, client=None, **fields):
        return (client or self.staff).post(self.list_url, self.body(**fields), format='json')

    def detail(self, order_id, action=None):
        return reverse(f'salesorder-{action}' if action else 'salesorder-detail', args=[order_id])

    def operator(self, role):
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', role)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        return api


class OrderApiTests(Base):
    def test_create_copies_the_default_address(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual((data['number'], data['number_display'], data['status']), (1, 'SO-000001', 'draft'))
        self.assertEqual((data['delivery_label'], data['line1'], data['postcode']),
                         ('Kitchen door', '1 Fake Street', 'TT1 1TT'))
        self.assertEqual((data['customer_code'], data['customer_name']), ('C-1', 'Fake Bistro'))
        self.assertFalse(data['off_delivery_day'])
        self.assertEqual([(line['quantity'], line['weight_kg'], line['unit_price']) for line in data['lines']],
                         [(4, None, None), (None, '2.500', None), (2, None, None)])
        order = SalesOrder.objects.get(pk=data['id'])
        self.assertEqual((order.company, order.created_by), (self.company, self.staff_user))

    def test_chosen_address_and_address_rules(self):
        response = self.create(address=self.second.pk)
        self.assertEqual(response.json()['line1'], '2 Fake Road')
        self.assertEqual(self.create(address=self.foreign_address.pk).status_code, 400)
        other = TradeCustomer.objects.create(company=self.company, account_code='C-2', legal_name='E2E Fake Two')
        theirs = TradeCustomerAddress.objects.create(customer=other, label='X', line1='3 Fake Way', town='T',
                                                     postcode='TT3 3TT', is_default=True)
        self.assertEqual(self.create(address=theirs.pk).status_code, 400)  # another customer's address
        no_default = TradeCustomer.objects.create(company=self.company, account_code='C-3', legal_name='E2E Fake 3')
        self.assertEqual(self.create(customer=no_default.pk).status_code, 400)

    def test_the_copy_survives_editing_the_customers_addresses(self):
        order_id = self.create().json()['id']
        patch = self.staff.patch(reverse('tradecustomer-detail', args=[self.customer.pk]), {'addresses': [
            {'label': 'Moved', 'line1': '5 New Fake Street', 'town': 'Newtown', 'postcode': 'TT5 5TT'}]},
            format='json')
        self.assertEqual(patch.status_code, 200, patch.content)
        self.assertEqual(self.staff.get(self.detail(order_id)).json()['line1'], '1 Fake Street')

    def test_a_closed_customer_gets_no_new_orders_on_hold_may_draft(self):
        TradeCustomer.objects.filter(pk=self.customer.pk).update(status='closed')
        self.assertEqual(self.create().status_code, 400)
        TradeCustomer.objects.filter(pk=self.customer.pk).update(status='on_hold')
        order_id = self.create().json()['id']
        response = self.staff.post(self.detail(order_id, 'confirm'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('on hold', response.json()['detail'])

    def test_line_rules(self):
        cases = [
            [{'product': self.each.pk}],
            [{'product': self.each.pk, 'quantity': 0}],
            [{'product': self.each.pk, 'quantity': 1, 'weight_kg': '1.000'}],
            [{'product': self.loose.pk, 'quantity': 1}],
            [{'product': self.loose.pk, 'weight_kg': '0'}],
            [{'product': self.loose.pk, 'weight_kg': '1.0005'}],
            [{'product': self.catch.pk, 'weight_kg': '2.000'}],
            [{'product': self.catch.pk, 'quantity': 1, 'weight_kg': '2.000'}],
            [{'product': self.each.pk, 'quantity': 1}, {'product': self.each.pk, 'quantity': 2}],
            [{'product': self.foreign_product.pk, 'quantity': 1}],
            [],
        ]
        for lines in cases:
            with self.subTest(lines=lines):
                self.assertEqual(self.create(lines=lines).status_code, 400)
        self.assertFalse(SalesOrder.objects.exists())

    def test_patched_lines_are_whole_lines(self):
        order_id = self.create().json()['id']
        response = self.staff.patch(self.detail(order_id), {'lines': [{'quantity': 3}]}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('product', response.json()['lines'][0])

    def test_trade_sized_caps(self):
        for line in ({'product': self.each.pk, 'quantity': 3_000_000_000},
                     {'product': self.each.pk, 'quantity': 100_001},
                     {'product': self.loose.pk, 'weight_kg': '10000.001'}):
            with self.subTest(line=line):
                self.assertEqual(self.create(lines=[line]).status_code, 400)
        self.assertEqual(self.create(lines=[{'product': self.each.pk, 'quantity': 100_000},
                                            {'product': self.loose.pk, 'weight_kg': '10000.000'}]).status_code, 201)

    def test_draft_edits_replace_lines_then_no_edits_after_confirm(self):
        order_id = self.create().json()['id']
        url = self.detail(order_id)
        response = self.staff.patch(url, {'lines': [{'product': self.each.pk, 'quantity': 10}],
                                          'delivery_date': '2026-11-11', 'address': self.second.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual([line['quantity'] for line in data['lines']], [10])
        self.assertEqual((data['line1'], data['off_delivery_day']), ('2 Fake Road', True))  # a Wednesday
        self.assertEqual(self.staff.patch(url, {'customer': self.foreign_customer.pk}, format='json').json()[
            'customer'], self.customer.pk)  # the customer is fixed once the order exists

        confirmed = self.staff.post(self.detail(order_id, 'confirm'))
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(confirmed.json()['status'], 'confirmed')
        self.assertEqual(confirmed.json()['lines'][0]['unit_price'], '2.5000')
        self.assertEqual(confirmed.json()['estimated_gross'], '25.00')
        for change in ({'note': 'x'}, {'lines': [{'product': self.each.pk, 'quantity': 1}]}):
            with self.subTest(change=list(change)):
                self.assertEqual(self.staff.patch(url, change, format='json').status_code, 400)
        self.assertEqual(self.staff.post(self.detail(order_id, 'confirm')).status_code, 400)

    def test_cancel(self):
        order_id = self.create().json()['id']
        self.assertEqual(self.staff.post(self.detail(order_id, 'cancel'), {}, format='json').status_code, 400)
        response = self.staff.post(self.detail(order_id, 'cancel'), {'reason': 'Rang to cancel'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()['status'], response.json()['cancel_reason']),
                         ('cancelled', 'Rang to cancel'))
        again = self.staff.post(self.detail(order_id, 'cancel'), {'reason': 'Again'}, format='json')
        self.assertEqual(again.status_code, 400)

    def test_no_delete(self):
        order_id = self.create().json()['id']
        self.assertEqual(self.staff.delete(self.detail(order_id)).status_code, 405)

    def test_roles(self):
        order_id = self.create().json()['id']
        self.assertEqual(self.member.get(self.detail(order_id)).status_code, 200)
        self.assertEqual(self.create(self.member).status_code, 403)
        self.assertEqual(self.member.post(self.detail(order_id, 'confirm')).status_code, 403)
        self.assertEqual(self.member.post(self.detail(order_id, 'cancel'), {'reason': 'x'},
                                          format='json').status_code, 403)
        for role in (TILL, STOCK):
            self.assertEqual(self.operator(role).get(self.list_url).status_code, 403)

    def test_tenancy(self):
        theirs = SalesOrder.objects.create(company=self.other_company, number=1, customer=self.foreign_customer,
                                           delivery_date='2026-11-13', line1='9 Fake Lane', town='Elsewhere',
                                           postcode='TT9 9TT')
        self.assertEqual(self.staff.get(self.detail(theirs.pk)).status_code, 404)
        self.assertEqual(self.staff.post(self.detail(theirs.pk, 'confirm')).status_code, 404)
        self.assertEqual(self.create(customer=self.foreign_customer.pk).status_code, 400)
        response = self.create(company=self.other_company.pk)
        self.assertEqual(SalesOrder.objects.get(pk=response.json()['id']).company, self.company)
        self.assertEqual(response.json()['number'], 1)  # numbers are per company
        self.assertEqual(self.staff.get(self.list_url).json()['count'], 1)

    def test_superuser_stays_in_their_company(self):
        admin = APIClient()
        admin.force_authenticate(UserFactory(company=self.company, is_staff=True, is_superuser=True))
        self.assertEqual(self.create(admin, customer=self.foreign_customer.pk).status_code, 400)
        self.assertEqual(self.create(admin, lines=[{'product': self.foreign_product.pk, 'quantity': 1}]).status_code,
                         400)
        self.assertEqual(self.create(admin, address=self.foreign_address.pk).status_code, 400)
        self.assertEqual(self.create(admin).status_code, 201)

    def test_filters(self):
        first = self.create().json()['id']
        second = self.create(delivery_date='2026-11-20').json()['id']
        self.staff.post(self.detail(second, 'confirm'))

        def ids(**params):
            response = self.member.get(self.list_url, params)
            self.assertEqual(response.status_code, 200, response.content)
            return sorted(o['id'] for o in response.json()['results'])

        self.assertEqual(ids(status='draft'), [first])
        self.assertEqual(ids(customer=self.customer.pk), [first, second])
        self.assertEqual(ids(delivery_date='2026-11-20'), [second])
        self.assertEqual(ids(**{'from': '2026-11-14'}), [second])
        self.assertEqual(ids(to='2026-11-14'), [first])
        for bad in ({'status': 'nope'}, {'customer': 'x'}, {'from': '13/11/2026'}):
            with self.subTest(bad=bad):
                self.assertEqual(self.member.get(self.list_url, bad).status_code, 400)


class OrderAdminTests(Base):
    def setUp(self):
        super().setUp()
        staff = UserFactory(company=self.company, is_staff=True)
        staff.user_permissions.set(Permission.objects.filter(content_type__app_label='wholesale'))
        self.client.force_login(staff)
        self.mine = SalesOrder.objects.get(pk=self.create().json()['id'])
        self.theirs = SalesOrder.objects.create(company=self.other_company, number=1, customer=self.foreign_customer,
                                                delivery_date='2026-11-13', line1='9 Fake Lane', town='Elsewhere',
                                                postcode='TT9 9TT')

    def test_scoped_and_read_only(self):
        listing = self.client.get(reverse('admin:wholesale_salesorder_changelist'))
        self.assertContains(listing, 'SO-000001')
        self.assertNotContains(listing, 'Elsewhere')
        self.assertEqual(self.client.get(reverse('admin:wholesale_salesorder_change',
                                                 args=[self.theirs.pk])).status_code, 302)
        page = self.client.get(reverse('admin:wholesale_salesorder_change', args=[self.mine.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'name="_save"')
        self.assertEqual(self.client.get(reverse('admin:wholesale_salesorder_add')).status_code, 403)
