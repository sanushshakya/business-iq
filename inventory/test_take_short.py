# inventory/test_take_short.py

"""G28: stock sold offline that the shop no longer had is booked in from ADJUSTMENT and out to the customer, through a
SHORT batch, so batches never go below 0 and every movement balances."""

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.test import TestCase

from common.tests.factories import CompanyFactory, ProductFactory
from inventory.ledger import take_short, virtual_location
from inventory.models import Location, Product, StockBatch, StockMovement
from tenants.models import Branch

D = Decimal


class TakeShortTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=branch, type='shop_floor')
        self.customer = virtual_location(self.company, Location.CUSTOMER)
        self.adjustment = virtual_location(self.company, Location.ADJUSTMENT)

    def short(self, product, **kwargs):
        with transaction.atomic():
            return take_short(product, location=self.shop, destination=self.customer, reference='S-000042',
                              expiry=date(2026, 11, 4), **kwargs)

    def test_each_product(self):
        rice = ProductFactory(company=self.company, stock_quantity=0)
        batch, pieces, kg = self.short(rice, quantity=2, weight_kg=None)
        self.assertEqual((batch.batch_number, batch.location, pieces, kg), ('SHORT-S-000042', self.shop, 2, None))
        batch.refresh_from_db()
        self.assertEqual((batch.quantity, batch.expiration_date), (0, date(2026, 11, 4)))
        moves = list(StockMovement.objects.filter(batch=batch).order_by('pk').values_list(
            'movement_type', 'quantity', 'source_location', 'destination_location'))
        self.assertEqual(moves, [('inbound', 2, self.adjustment.pk, self.shop.pk),
                                 ('outbound', 2, self.shop.pk, self.customer.pk)])
        self.assertEqual(Product.objects.get(pk=rice.pk).stock_quantity, 0)  # in and out: unchanged

    def test_loose_kg(self):
        mince = ProductFactory(company=self.company, sold_by='weight', price_per_kg=D('8.9900'), stock_quantity=0)
        batch, pieces, kg = self.short(mince, quantity=0, weight_kg=D('0.750'))
        batch.refresh_from_db()
        self.assertEqual((pieces, kg, batch.weight_kg), (0, D('0.750'), D('0.000')))

    def test_catch_weight_pieces_and_kg(self):
        chops = ProductFactory(company=self.company, sold_by='weight', is_catch_weight=True,
                               price_per_kg=D('12.0000'), stock_quantity=0)
        batch, pieces, kg = self.short(chops, quantity=3, weight_kg=D('1.230'))
        batch.refresh_from_db()
        self.assertEqual((pieces, kg, batch.quantity, batch.weight_kg), (3, D('1.230'), 0, D('0.000')))

    def test_same_reference_reuses_the_batch(self):
        rice = ProductFactory(company=self.company, stock_quantity=0)
        first, *_ = self.short(rice, quantity=1, weight_kg=None)
        second, *_ = self.short(rice, quantity=1, weight_kg=None)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(StockBatch.objects.filter(batch_number='SHORT-S-000042').count(), 1)
