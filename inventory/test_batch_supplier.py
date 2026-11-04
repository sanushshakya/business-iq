# inventory/test_batch_supplier.py

"""G24: the supplier a batch was received from, passed on to breakdown outputs and transferred lots."""

from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from inventory.ledger import move, transfer_batch, virtual_location
from inventory.models import Location, StockBatch, Supplier
from inventory.test_breakdowns import BreakdownTestCase
from tenants.models import Branch
from tenants.roles import STOCK


class BatchSupplierApiTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.product = ProductFactory(company=self.company)
        self.supplier = Supplier.objects.create(company=self.company, name='Fake Farms Ltd')
        self.api = APIClient()
        self.api.force_authenticate(UserFactory(company=self.company, is_staff=True))

    def receive(self, **fields):
        body = {'product': self.product.pk, 'batch_number': 'LOT-1', 'quantity': 5, 'expiration_date': '2030-01-31',
                **fields}
        return self.api.post(reverse('stockbatch-list'), body, format='json')

    def test_supplier_round_trips(self):
        response = self.receive(supplier=self.supplier.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['supplier'], self.supplier.pk)
        self.assertEqual(StockBatch.objects.get().supplier, self.supplier)

    def test_supplier_is_optional(self):
        response = self.receive()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(response.json()['supplier'])

    def test_other_companys_supplier_is_400(self):
        theirs = Supplier.objects.create(company=CompanyFactory(), name='Fake Other Farms')
        response = self.receive(supplier=theirs.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn('supplier', response.json())


    def test_supplier_cannot_be_changed_after_receipt(self):
        batch = self.receive(supplier=self.supplier.pk).json()
        other = Supplier.objects.create(company=self.company, name='Fake Other Farm')
        self.api.patch(reverse('stockbatch-detail', args=[batch['id']]), {'supplier': other.pk}, format='json')
        self.assertEqual(StockBatch.objects.get(pk=batch['id']).supplier, self.supplier)

    def test_stock_role_sees_only_supplier_names(self):
        Supplier.objects.filter(pk=self.supplier.pk).update(contact_info='Fake Contact 01234', address='1 Fake Lane')
        stock = APIClient()
        stock.force_authenticate(UserFactory(company=self.company, role=STOCK))
        [row] = stock.get(reverse('supplier-list')).json()['results']
        self.assertEqual(row, {'id': self.supplier.pk, 'name': 'Fake Farms Ltd'})
        [row] = self.api.get(reverse('supplier-list')).json()['results']  # staff still see everything
        self.assertEqual(row['address'], '1 Fake Lane')

    def test_supplier_with_batches_cannot_be_deleted(self):
        self.receive(supplier=self.supplier.pk)
        response = self.api.delete(reverse('supplier-detail', args=[self.supplier.pk]))
        self.assertEqual((response.status_code, response.json()['code']), (409, 'Protected'))  # common/exceptions.py
        self.assertTrue(Supplier.objects.filter(pk=self.supplier.pk).exists())


class SupplierPassedOnTests(BreakdownTestCase):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(company=self.company, name='Fake Farms Ltd')
        StockBatch.objects.filter(pk=self.input.pk).update(supplier=self.supplier)
        self.input.refresh_from_db()

    def test_breakdown_outputs_keep_the_input_supplier(self):
        breakdown, _ = self.record()
        suppliers = {line.output_batch.supplier for line in breakdown.lines.all() if line.output_batch}
        self.assertEqual(suppliers, {self.supplier})

    def test_transferred_lot_keeps_the_supplier(self):
        product = ProductFactory(company=self.company)
        batch = StockBatch.objects.create(product=product, batch_number='EACH-1', quantity=0, location=self.shop,
                                          expiration_date=self.today, supplier=self.supplier)
        with transaction.atomic():
            move(batch, 4, 'inbound', counterpart=virtual_location(self.company, Location.SUPPLIER))
            batch = StockBatch.objects.select_for_update().get(pk=batch.pk)
            held = transfer_batch(batch, 2, self.butchery)
        held.refresh_from_db()
        self.assertEqual((held.supplier, held.quantity, held.location), (self.supplier, 2, self.butchery))
