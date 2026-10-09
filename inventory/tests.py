# inventory/tests.py

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.models import Product, ProductCategory


class InventoryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_requires_authentication(self):
        self.assertIn(self.client.get(reverse('product-list')).status_code, (401, 403))

    def test_product_crud(self):
        self.client.force_authenticate(UserFactory())
        category = ProductCategory.objects.create(name='Fruit')
        response = self.client.post(
            reverse('product-list'),
            {'name': 'Apple', 'description': 'Red', 'price': '1.50', 'stock_quantity': 10, 'category': category.pk},
        )
        self.assertEqual(response.status_code, 201, response.content)
        pk = response.json()['id']

        response = self.client.patch(reverse('product-detail', args=[pk]), {'price': '2.00'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(Product.objects.get(pk=pk).price), '2.00')

        self.assertEqual(self.client.delete(reverse('product-detail', args=[pk])).status_code, 204)

    def test_stock_batch_and_movement(self):
        user = UserFactory()
        self.client.force_authenticate(user)
        batch = StockBatchFactory(product__company=user.company)
        response = self.client.post(
            reverse('stockmovement-list'), {'batch': batch.pk, 'quantity': 3, 'movement_type': 'inbound'}
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.client.get(reverse('stockbatch-list')).status_code, 200)

    def test_str(self):
        self.assertEqual(str(ProductFactory(name='Pear')), 'Pear')


class BatchNumberUniquenessTests(TestCase):
    def setUp(self):
        self.user = UserFactory()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.product = ProductFactory(company=self.user.company)

    def payload(self, product, number='B-1'):
        return {'product': product.pk, 'batch_number': number, 'quantity': 5, 'expiration_date': '2027-01-01'}

    def test_other_companies_can_use_the_same_batch_number(self):
        self.assertEqual(self.client.post(reverse('stockbatch-list'), self.payload(self.product)).status_code, 201)

        other = UserFactory()
        other_client = APIClient()
        other_client.force_authenticate(other)
        theirs = ProductFactory(company=other.company)
        response = other_client.post(reverse('stockbatch-list'), self.payload(theirs))
        self.assertEqual(response.status_code, 201, response.content)

    def test_duplicates_within_a_product_are_a_clean_400(self):
        self.client.post(reverse('stockbatch-list'), self.payload(self.product))
        response = self.client.post(reverse('stockbatch-list'), self.payload(self.product))
        self.assertEqual(response.status_code, 400, response.content)

    def test_the_same_number_on_two_of_my_products_is_allowed(self):
        second = ProductFactory(company=self.user.company)
        self.assertEqual(self.client.post(reverse('stockbatch-list'), self.payload(self.product)).status_code, 201)
        self.assertEqual(self.client.post(reverse('stockbatch-list'), self.payload(second)).status_code, 201)


class StockProjectionTests(TestCase):
    """Stock 30, selling 2 a day (60 units over the 30 day history), reorder threshold 5."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from demand_calendar.models import Event
        from inventory.models import ProductCategory, StockMovement

        self.timedelta, self.Event, self.today = timedelta, Event, timezone.localdate()
        self.user = UserFactory()
        self.category = ProductCategory.objects.create(name='Dried fruit')
        self.product = ProductFactory(company=self.user.company, category=self.category, stock_quantity=30, reorder_threshold=5)
        StockMovement.objects.create(batch=StockBatchFactory(product=self.product), quantity=60, movement_type='outbound')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def url(self, product=None):
        return reverse('product-stock-projection', args=[(product or self.product).pk])

    def day(self, offset):
        return (self.today + self.timedelta(days=offset)).isoformat()

    def test_steady_demand(self):
        body = self.client.get(self.url(), {'days': 20}).json()
        self.assertEqual((body['current_stock'], body['average_daily_demand'], body['days_of_cover']), (30, '2.00', '15.00'))
        self.assertEqual(body['reorder_date'], self.day(12))   # 30 - 2 x 13 = 4, at or under 5
        self.assertEqual(body['stockout_date'], self.day(14))  # 30 - 2 x 15 = 0
        rows = body['projection']
        self.assertEqual(len(rows), 20)
        self.assertEqual((rows[0]['date'], rows[0]['projected_quantity'], rows[0]['demand_multiplier']), (self.day(0), '28.00', '1.00'))
        self.assertEqual(rows[14]['projected_quantity'], '0.00')
        self.assertEqual(rows[19]['projected_quantity'], '0.00')   # never goes negative

    def test_calendar_events_bring_the_run_out_forward(self):
        event = self.Event.objects.create(
            name='Surge', start_date=self.today + self.timedelta(days=2), end_date=self.today + self.timedelta(days=3),
            demand_multiplier=2)
        event.product_categories.set([self.category])
        body = self.client.get(self.url(), {'days': 20}).json()
        multipliers = {r['date']: r['demand_multiplier'] for r in body['projection']}
        self.assertEqual((multipliers[self.day(1)], multipliers[self.day(2)], multipliers[self.day(3)], multipliers[self.day(4)]),
                         ('1.00', '2.00', '2.00', '1.00'))
        self.assertEqual(body['stockout_date'], self.day(12))   # 2 + 2 + 4 + 4 + 2 x 9 = 30
        self.assertEqual(body['reorder_date'], self.day(10))

    def test_events_for_other_categories_are_ignored(self):
        from inventory.models import ProductCategory

        event = self.Event.objects.create(
            name='Other', start_date=self.today, end_date=self.today + self.timedelta(days=5), demand_multiplier=3)
        event.product_categories.set([ProductCategory.objects.create(name='Drinks')])
        self.assertEqual(self.client.get(self.url()).json()['stockout_date'], self.day(14))

    def test_no_sales_history_means_no_run_out_date(self):
        quiet = ProductFactory(company=self.user.company, stock_quantity=30)
        body = self.client.get(self.url(quiet)).json()
        self.assertEqual((body['average_daily_demand'], body['days_of_cover'], body['stockout_date']), ('0.00', None, None))
        self.assertTrue(all(r['projected_quantity'] == '30.00' for r in body['projection']))

    def test_the_default_horizon_is_30_days(self):
        self.assertEqual(len(self.client.get(self.url()).json()['projection']), 30)

    def test_days_must_be_between_1_and_180(self):
        for bad in ('0', '181', '-3', 'abc', ''):
            response = self.client.get(self.url(), {'days': bad})
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn('days', response.json())
        self.assertEqual(self.client.get(self.url(), {'days': 180}).status_code, 200)

    def test_other_companies_products_are_not_reachable(self):
        theirs = ProductFactory(company=CompanyFactory())
        self.assertEqual(self.client.get(self.url(theirs)).status_code, 404)
        self.assertIn(APIClient().get(self.url()).status_code, (401, 403))
