# inventory/test_catch_weight.py

"""G14: products sold by weight — product rules, and receiving or moving stock in kg."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.models import Location, StockBatch, StockMovement
from tenants.models import Branch


class WeighedProductTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=self.branch, type='shop_floor')
        self.api = APIClient()
        self.api.force_authenticate(UserFactory(company=self.company, is_staff=True))

    def create(self, **fields):
        body = {'name': 'Fake Mince', 'description': 'x', 'price': '0.00', **fields}
        return self.api.post(reverse('product-list'), body, format='json')

    def test_product_api_shows_new_fields(self):
        response = self.create(sold_by='weight', price_per_kg='8.9900', is_catch_weight=True,
                               nominal_weight_kg='0.450')
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body['sold_by'], body['price_per_kg'], body['is_catch_weight'], body['nominal_weight_kg']),
                         ('weight', '8.9900', True, '0.450'))
        self.assertEqual(self.create(name='Fake Tin').json()['sold_by'], 'each')

    def test_weight_needs_price_per_kg(self):
        response = self.create(sold_by='weight')
        self.assertEqual(response.status_code, 400)
        self.assertIn('price_per_kg', response.json())
        self.assertEqual(self.create(sold_by='weight', price_per_kg='0').status_code, 400)

    def test_catch_weight_needs_weight(self):
        response = self.create(is_catch_weight=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn('is_catch_weight', response.json())

    def test_sold_by_locked_while_stock_held(self):
        product = ProductFactory(company=self.company)
        StockBatchFactory(product=product, quantity=3)
        url = reverse('product-detail', args=[product.pk])
        response = self.api.patch(url, {'sold_by': 'weight', 'price_per_kg': '5.0000'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('sold_by', response.json())
        StockBatch.objects.update(quantity=0)
        self.assertEqual(self.api.patch(url, {'sold_by': 'weight', 'price_per_kg': '5.0000'}, format='json')
                         .status_code, 200)
        # Other edits are fine while stock is held.
        StockBatch.objects.update(weight_kg=Decimal('1.000'))
        self.assertEqual(self.api.patch(url, {'name': 'Fake Lamb'}, format='json').status_code, 200)
        response = self.api.patch(url, {'is_catch_weight': True}, format='json')
        self.assertEqual(response.status_code, 400)


class WeighedReceivingTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=self.branch, type='shop_floor')
        self.loose = ProductFactory(company=self.company, sold_by='weight', price_per_kg=Decimal('8.0000'))
        self.packs = ProductFactory(company=self.company, sold_by='weight', is_catch_weight=True,
                                    price_per_kg=Decimal('9.0000'))
        self.each = ProductFactory(company=self.company)
        self.api = APIClient()
        self.api.force_authenticate(UserFactory(company=self.company, is_staff=True))

    def receive(self, product, **fields):
        body = {'product': product.pk, 'batch_number': 'LOT-1', 'expiration_date': '2030-01-31', **fields}
        return self.api.post(reverse('stockbatch-list'), body, format='json')

    def test_loose_receipt_in_kg(self):
        response = self.receive(self.loose, quantity=0, weight_kg='12.500')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['weight_kg'], '12.500')

    def test_weighed_receipt_needs_kg(self):
        for product, quantity in ((self.loose, 0), (self.packs, 4)):
            response = self.receive(product, quantity=quantity)
            self.assertEqual(response.status_code, 400, product)
            self.assertIn('weight_kg', response.json())

    def test_loose_receipt_has_no_count(self):
        response = self.receive(self.loose, quantity=3, weight_kg='2.000')
        self.assertEqual(response.status_code, 400)
        self.assertIn('quantity', response.json())

    def test_catch_weight_receipt_counts_pieces_and_kg(self):
        response = self.receive(self.packs, quantity=4, weight_kg='1.840')
        self.assertEqual(response.status_code, 201, response.content)
        batch = StockBatch.objects.get()
        self.assertEqual((batch.quantity, batch.weight_kg), (4, Decimal('1.840')))

    def test_each_receipt_refuses_kg_and_is_unchanged(self):
        self.assertEqual(self.receive(self.each, quantity=5, weight_kg='1.000').status_code, 400)
        response = self.receive(self.each, quantity=5)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['weight_kg'], '0.000')

    def move(self, batch, movement_type, **fields):
        return self.api.post(reverse('stockmovement-list'),
                             {'batch': batch.pk, 'movement_type': movement_type, **fields}, format='json')

    def test_loose_movement_in_kg(self):
        batch = StockBatchFactory(product=self.loose, quantity=0, weight_kg=Decimal('2.000'))
        response = self.move(batch, 'outbound', quantity=0, weight_kg='0.750')
        self.assertEqual(response.status_code, 201, response.content)
        batch.refresh_from_db()
        self.assertEqual(batch.weight_kg, Decimal('1.250'))
        self.assertEqual(StockMovement.objects.get().weight_kg, Decimal('0.750'))
        self.assertEqual(self.move(batch, 'inbound', quantity=0).status_code, 400)  # kg required

    def test_outbound_cannot_take_more_kg_than_the_batch(self):
        batch = StockBatchFactory(product=self.loose, quantity=0, weight_kg=Decimal('1.000'))
        response = self.move(batch, 'outbound', quantity=0, weight_kg='1.001')
        self.assertEqual(response.status_code, 400)
        self.assertIn('weight_kg', response.json())

    def test_catch_weight_movement_can_be_kg_only(self):
        batch = StockBatchFactory(product=self.packs, quantity=2, weight_kg=Decimal('1.000'))
        response = self.move(batch, 'outbound', quantity=0, weight_kg='0.100')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.move(batch, 'outbound', quantity=0, weight_kg='0').status_code, 400)  # nothing moved

    def test_each_movement_refuses_kg_and_needs_a_count(self):
        batch = StockBatchFactory(product=self.each, quantity=5)
        self.assertEqual(self.move(batch, 'outbound', quantity=1, weight_kg='0.100').status_code, 400)
        self.assertEqual(self.move(batch, 'outbound', quantity=0).status_code, 400)
        self.assertEqual(self.move(batch, 'outbound', quantity=1).status_code, 201)
        self.assertIsNone(StockMovement.objects.get().weight_kg)


class WeighedReviewTests(WeighedReceivingTests):
    def test_last_pieces_must_take_all_their_kg(self):
        batch = StockBatchFactory(product=self.packs, quantity=2, weight_kg=Decimal('1.000'))
        url = reverse('stockmovement-list')
        response = self.api.post(url, {'batch': batch.pk, 'movement_type': 'outbound', 'quantity': 2,
                                       'weight_kg': '0.600'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('weight_kg', response.json())
        response = self.api.post(url, {'batch': batch.pk, 'movement_type': 'outbound', 'quantity': 2,
                                       'weight_kg': '1.000'}, format='json')
        self.assertEqual(response.status_code, 201, response.content)

    def test_kind_locked_while_a_sale_can_still_be_voided(self):
        from sales.models import SaleAllocation, SaleItem
        from sales.tests.test_sales import make_till
        from sales.models import Sale, TillSession

        till = make_till(self.company)
        user = UserFactory(company=self.company)
        session = TillSession.objects.create(till=till, opened_by=user)
        sale = Sale.objects.create(session=session, cashier=user, payment_method='card', total=Decimal('1.00'),
                                   amount_tendered=Decimal('1.00'))
        batch = StockBatchFactory(product=self.each, quantity=0)
        item = SaleItem.objects.create(sale=sale, product=self.each, product_name='x', quantity=1,
                                       unit_price=Decimal('1.00'), line_total=Decimal('1.00'))
        SaleAllocation.objects.create(item=item, batch=batch, quantity=1)
        url = reverse('product-detail', args=[self.each.pk])
        body = {'sold_by': 'weight', 'price_per_kg': '5.0000'}
        response = self.api.patch(url, body, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('sold_by', response.json())
        session.closed_at = timezone.now()
        session.save()
        self.assertEqual(self.api.patch(url, body, format='json').status_code, 200)
