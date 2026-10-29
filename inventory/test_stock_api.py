# inventory/test_stock_api.py

"""G09: receiving stock at a location through the API, and movements recorded by the ledger."""

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.models import Location, StockBatch, StockMovement
from tenants.models import Branch

class BatchLocationApiTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=self.branch, type='shop_floor')
        self.product = ProductFactory(company=self.company)
        self.api = APIClient()
        self.api.force_authenticate(UserFactory(company=self.company, is_staff=True))

    def receive(self, **fields):
        body = {'product': self.product.pk, 'batch_number': 'LOT-1', 'quantity': 5, 'expiration_date': '2030-01-31',
                **fields}
        return self.api.post(reverse('stockbatch-list'), body, format='json')

    def test_receipt_at_a_chosen_location(self):
        warehouse = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse',
                                            type='warehouse')
        response = self.receive(location=warehouse.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(StockBatch.objects.get().location, warehouse)

    def test_receipt_defaults_to_the_only_shop_floor(self):
        response = self.receive()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['location'], self.shop.pk)

    def test_receipt_without_location_is_400_when_ambiguous(self):
        Branch.objects.create(company=self.company, name='Fake Second', address='x')
        response = self.receive()
        self.assertEqual(response.status_code, 400)
        self.assertIn('location', response.json())

    def test_other_companys_location_is_400(self):
        theirs = Location.objects.get(company=CompanyFactory(), type='waste')
        self.assertEqual(self.receive(location=theirs.pk).status_code, 400)

    def test_virtual_location_is_400(self):
        waste = Location.objects.get(company=self.company, type='waste')
        self.assertEqual(self.receive(location=waste.pk).status_code, 400)

    def test_location_cannot_be_changed_by_editing_the_batch(self):
        batch = self.receive().json()
        warehouse = Location.objects.create(company=self.company, code='WH-1', name='x', type='warehouse')
        self.api.patch(reverse('stockbatch-detail', args=[batch['id']]), {'location': warehouse.pk}, format='json')
        self.assertEqual(StockBatch.objects.get(pk=batch['id']).location, self.shop)

    def test_same_lot_number_at_two_locations(self):
        warehouse = Location.objects.create(company=self.company, code='WH-1', name='x', type='warehouse')
        self.assertEqual(self.receive().status_code, 201)
        self.assertEqual(self.receive(location=warehouse.pk).status_code, 201)
        self.assertEqual(self.receive().status_code, 400)  # same lot, same place

class MovementApiTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.batch = StockBatchFactory(product=ProductFactory(company=self.company, stock_quantity=10), quantity=10)
        self.api = APIClient()
        self.api.force_authenticate(UserFactory(company=self.company, is_staff=True))

    def post(self, movement_type, quantity, **extra):
        body = {'batch': self.batch.pk, 'quantity': quantity, 'movement_type': movement_type, **extra}
        return self.api.post(reverse('stockmovement-list'), body, format='json')

    def test_inbound_comes_from_supplier(self):
        response = self.post('inbound', 4)
        self.assertEqual(response.status_code, 201, response.content)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.source_location.type, movement.destination_location), ('supplier', self.batch.location))
        self.assertEqual(response.json()['destination_location'], self.batch.location_id)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 14)

    def test_outbound_goes_to_adjustment(self):
        self.assertEqual(self.post('outbound', 3).status_code, 201)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.source_location, movement.destination_location.type), (self.batch.location, 'adjustment'))

    def test_locations_in_the_body_are_ignored(self):
        waste = Location.objects.get(company=self.company, type='waste')
        self.post('outbound', 1, source_location=waste.pk, destination_location=waste.pk)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.source_location, movement.destination_location.type), (self.batch.location, 'adjustment'))

    def test_outbound_larger_than_batch_is_400(self):
        self.assertEqual(self.post('outbound', 11).status_code, 400)
        self.assertFalse(StockMovement.objects.exists())

class CatalogueOpeningBatchTests(TestCase):
    def test_primary_location_creates_main_only_when_asked(self):
        from inventory.ledger import primary_location

        company = CompanyFactory()
        self.assertIsNone(primary_location(company))
        main = primary_location(company, create=True)
        self.assertEqual((main.code, main.sellable, main.online_sellable), ('MAIN', True, True))
        self.assertEqual(primary_location(company, create=True), main)

class StockReviewFixTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=self.branch, type='shop_floor')
        self.product = ProductFactory(company=self.company)
        self.api = APIClient()
        self.api.force_authenticate(UserFactory(company=self.company, is_staff=True))

    def receive(self, **fields):
        body = {'product': self.product.pk, 'batch_number': 'LOT-1', 'quantity': 5, 'expiration_date': '2030-01-31',
                **fields}
        return self.api.post(reverse('stockbatch-list'), body, format='json')

    def test_inactive_location_is_400(self):
        retired = Location.objects.create(company=self.company, code='OLD', name='x', type='warehouse',
                                          is_active=False)
        self.assertEqual(self.receive(location=retired.pk).status_code, 400)

    def test_non_object_body_is_400(self):
        self.assertEqual(self.api.post(reverse('stockbatch-list'), [1, 2], format='json').status_code, 400)

    def test_put_without_location_keeps_it(self):
        batch = self.receive().json()
        body = {'product': self.product.pk, 'batch_number': 'LOT-1', 'quantity': 5, 'expiration_date': '2031-01-31'}
        response = self.api.put(reverse('stockbatch-detail', args=[batch['id']]), body, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['location'], self.shop.pk)

    def test_patch_with_a_taken_location_is_not_refused(self):
        warehouse = Location.objects.create(company=self.company, code='WH-1', name='x', type='warehouse')
        self.receive(location=warehouse.pk)
        batch = self.receive().json()  # same lot at the shop floor
        response = self.api.patch(reverse('stockbatch-detail', args=[batch['id']]),
                                  {'location': warehouse.pk, 'quantity': 4}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(StockBatch.objects.get(pk=batch['id']).location, self.shop)

    def test_move_refuses_a_batch_whose_location_is_another_companys(self):
        from django.db import transaction

        from inventory.ledger import LedgerError, move, virtual_location

        other = CompanyFactory()
        theirs = Location.objects.create(company=other, code='WH-X', name='x', type='warehouse')
        batch = StockBatchFactory(product=self.product, location=theirs, quantity=5)
        with self.assertRaises(LedgerError), transaction.atomic():
            move(batch, 1, 'outbound', counterpart=virtual_location(other, 'customer'))

    def test_admin_cannot_move_a_batch(self):
        from django.contrib import admin
        from django.test import RequestFactory

        model_admin = admin.site._registry[StockBatch]
        request = RequestFactory().get('/admin/')
        request.user = UserFactory(company=self.company, is_staff=True, is_superuser=True)
        batch = StockBatchFactory(product=self.product)
        self.assertIn('location', model_admin.get_readonly_fields(request, batch))
        self.assertNotIn('location', model_admin.get_readonly_fields(request, None))

class StockLockScopeTests(TestCase):
    """On Postgres, FOR UPDATE must lock only the batch rows, not the joined location (unrelated sales would wait)."""

    def test_take_fefo_locks_only_batches(self):

        from django.db import connection, transaction
        from django.test.utils import CaptureQueriesContext

        from inventory.ledger import take_fefo, virtual_location

        if connection.vendor != 'postgresql':
            self.skipTest('FOR UPDATE is only emitted on Postgres')
        company = CompanyFactory()
        Branch.objects.create(company=company, name='Fake', address='x')
        product = ProductFactory(company=company)
        batch = StockBatchFactory(product=product, quantity=5)
        with CaptureQueriesContext(connection) as queries, transaction.atomic():
            take_fefo(product, 1, locations=[batch.location], destination=virtual_location(company, 'customer'))
        locking = [q['sql'] for q in queries.captured_queries if 'FOR UPDATE' in q['sql']]
        self.assertTrue(locking)
        for sql in locking:
            self.assertIn('FOR UPDATE OF "inventory_stockbatch"', sql)
