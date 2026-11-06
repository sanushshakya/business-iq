"""G41: a confirmed trade order reserves warehouse stock; short lines are flagged and topped up; cancel releases.
All names are fake."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.ledger import move, virtual_location
from inventory.models import Location, Product, StockReservation
from wholesale.models import SalesOrder, TradeCustomer, TradeCustomerAddress

D = Decimal


class AllocationApiTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.company = CompanyFactory()
        self.staff = APIClient()
        self.staff.force_authenticate(UserFactory(company=self.company, is_staff=True))
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.warehouse = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse',
                                                 type=Location.WAREHOUSE)
        self.customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                     legal_name='E2E Fake Bistro Ltd')
        TradeCustomerAddress.objects.create(customer=self.customer, label='Kitchen', line1='1 Fake Street',
                                            town='Testtown', postcode='TT1 1TT', is_default=True)
        self.each = ProductFactory(company=self.company, stock_quantity=0, wholesale_price=D('2.0000'))
        self.loose = ProductFactory(company=self.company, stock_quantity=0, sold_by=Product.WEIGHT,
                                    price_per_kg=D('9.99'), wholesale_price=D('8.0000'))
        self.stock(self.each, 6)
        self.stock(self.loose, kg=D('3.000'))

    def stock(self, product, quantity=0, kg=None, days=30, location=None):
        batch = StockBatchFactory(product=product, quantity=0, weight_kg=D('0'), location=location or self.warehouse,
                                  expiration_date=self.today + timedelta(days=days))
        with transaction.atomic():
            move(batch, quantity, 'inbound', counterpart=virtual_location(self.company, 'supplier'),
                 weight_kg=kg if product.is_weighed else None)
        return batch

    def create(self, **fields):
        body = {'customer': self.customer.pk, 'delivery_date': str(self.today + timedelta(days=3)),
                'lines': [{'product': self.each.pk, 'quantity': 4}, {'product': self.loose.pk, 'weight_kg': '2.000'}],
                **fields}
        return self.staff.post(reverse('salesorder-list'), body, format='json')

    def post(self, order_id, action, client=None, **body):
        return (client or self.staff).post(reverse(f'salesorder-{action}', args=[order_id]), body, format='json')

    def test_confirm_reserves(self):
        created = self.create()
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()['source_location'], self.warehouse.pk)  # the only warehouse
        self.assertEqual([line['reserved'] for line in created.json()['lines']], [None, None])  # draft
        response = self.post(created.json()['id'], 'confirm')
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual([(line['reserved'], line['short']) for line in data['lines']],
                         [('4', '0'), ('2.000', '0.000')])
        self.assertFalse(data['is_short'])
        self.assertEqual(StockReservation.objects.filter(released_at__isnull=True).count(), 2)

    def test_short_then_allocate_tops_up(self):
        order_id = self.create(lines=[{'product': self.each.pk, 'quantity': 10}]).json()['id']
        data = self.post(order_id, 'confirm').json()
        self.assertEqual((data['lines'][0]['reserved'], data['lines'][0]['short'], data['is_short']),
                         ('6', '4', True))
        self.stock(self.each, 5, days=60)
        self.assertEqual(self.post(order_id, 'allocate', self.member).status_code, 403)
        data = self.post(order_id, 'allocate').json()
        self.assertEqual((data['lines'][0]['reserved'], data['lines'][0]['short'], data['is_short']),
                         ('10', '0', False))

    def test_cancel_releases(self):
        order_id = self.create().json()['id']
        self.post(order_id, 'confirm')
        response = self.post(order_id, 'cancel', reason='Fake reason')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(StockReservation.objects.filter(released_at__isnull=True).exists())
        self.assertEqual([line['reserved'] for line in response.json()['lines']], [None, None])

    def test_allocate_needs_a_confirmed_or_picking_order(self):
        order_id = self.create().json()['id']
        self.assertEqual(self.post(order_id, 'allocate').status_code, 400)  # draft
        self.post(order_id, 'cancel', reason='x')
        self.assertEqual(self.post(order_id, 'allocate').status_code, 400)

    def test_allocate_tenancy_and_operators(self):
        from django.contrib.auth import get_user_model

        from authentication import refresh_tokens
        from tenants.roles import STOCK

        theirs = SalesOrder.objects.create(company=CompanyFactory(), number=1, customer=TradeCustomer.objects.create(
            company=CompanyFactory(), account_code='T-1', legal_name='E2E Fake Theirs'), delivery_date=self.today,
            line1='9 Fake Lane', town='Elsewhere', postcode='TT9 9TT', status=SalesOrder.CONFIRMED)
        self.assertEqual(self.post(theirs.pk, 'allocate').status_code, 404)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', STOCK)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        order_id = self.create().json()['id']
        self.post(order_id, 'confirm')
        self.assertEqual(api.post(reverse('salesorder-allocate', args=[order_id])).status_code, 403)

    def test_lots_expiring_before_delivery_are_not_reserved(self):
        StockReservation.objects.all().delete()
        early = self.stock(self.each, 10, days=1)  # gone off before the delivery in 3 days
        order_id = self.create(lines=[{'product': self.each.pk, 'quantity': 8}]).json()['id']
        line = self.post(order_id, 'confirm').json()['lines'][0]
        self.assertEqual((line['reserved'], line['short']), ('6', '2'))
        self.assertFalse(StockReservation.objects.filter(batch=early).exists())

    def test_source_location_rules(self):
        second = Location.objects.create(company=self.company, code='WH-2', name='Fake warehouse two',
                                         type=Location.WAREHOUSE)
        response = self.create()
        self.assertEqual(response.status_code, 400)
        self.assertIn('source_location', response.json())
        self.assertEqual(self.create(source_location=second.pk).json()['source_location'], second.pk)
        shop = Location.objects.create(company=self.company, code='SHOP-2', name='Fake shop', type='shop_floor')
        retired = Location.objects.create(company=self.company, code='WH-OLD', name='Old', type=Location.WAREHOUSE,
                                          is_active=False)
        theirs = Location.objects.create(company=CompanyFactory(), code='WH-T', name='Theirs',
                                         type=Location.WAREHOUSE)
        for location in (shop, retired, theirs):
            with self.subTest(location=location.code):
                self.assertEqual(self.create(source_location=location.pk).status_code, 400)

    def test_source_location_is_fixed_after_draft(self):
        second = Location.objects.create(company=self.company, code='WH-2', name='Fake warehouse two',
                                         type=Location.WAREHOUSE)
        order_id = self.create(source_location=self.warehouse.pk).json()['id']
        url = reverse('salesorder-detail', args=[order_id])
        self.assertEqual(self.staff.patch(url, {'source_location': second.pk}, format='json').json()[
            'source_location'], second.pk)
        self.post(order_id, 'confirm')
        self.assertEqual(self.staff.patch(url, {'source_location': self.warehouse.pk}, format='json').status_code,
                         400)

    def test_confirm_without_a_source_is_refused(self):
        order_id = self.create().json()['id']
        SalesOrder.objects.filter(pk=order_id).update(source_location=None)
        response = self.post(order_id, 'confirm')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SalesOrder.objects.get(pk=order_id).status, 'draft')

    def test_list_query_count_does_not_grow_with_orders(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.staff.get(reverse('salesorder-list')).status_code, 200)
            return len(queries)

        self.post(self.create().json()['id'], 'confirm')
        one = count()
        for _ in range(3):
            self.post(self.create(lines=[{'product': self.each.pk, 'quantity': 1}]).json()['id'], 'confirm')
        self.assertEqual(count(), one)
