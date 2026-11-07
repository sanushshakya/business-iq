"""G42: picking moves a warehouse lot to DISPATCH at the real count and weight; the ledger still balances."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.test import TestCase
from django.utils import timezone

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory
from inventory.ledger import LedgerError, dispatch_location, move, pick_batch, virtual_location
from inventory.models import Location, Product, StockBatch, StockMovement, StockReservation

D = Decimal


class PickBatchTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse',
                                                 type=Location.WAREHOUSE)
        self.dispatch = dispatch_location(self.company)

    def batch(self, product, quantity=0, kg=None, number='LOT-1'):
        batch = StockBatchFactory(product=product, quantity=0, weight_kg=D('0'), location=self.warehouse,
                                  batch_number=number, expiration_date=timezone.localdate() + timedelta(days=10))
        with transaction.atomic():
            move(batch, quantity, 'inbound', counterpart=virtual_location(self.company, 'supplier'),
                 weight_kg=kg if product.is_weighed else None)
        return batch

    def pick(self, batch, **amounts):
        with transaction.atomic():
            locked = StockBatch.objects.select_for_update().select_related('location').get(pk=batch.pk)
            return pick_batch(locked, to=self.dispatch, **amounts)

    def kg_at(self, location, product):
        return StockBatch.objects.filter(location=location, product=product).aggregate(s=Sum('weight_kg'))['s']

    def assert_balances(self, product):
        """For every batch (at the warehouse and at DISPATCH): units and kg in - out = what it holds."""
        def total(rows, field):
            return rows.aggregate(s=Sum(field))['s'] or 0

        for batch in StockBatch.objects.filter(product=product):
            inflow = StockMovement.objects.filter(batch=batch, movement_type='inbound')
            outflow = StockMovement.objects.filter(batch=batch, movement_type='outbound')
            where = f'{batch.batch_number} at {batch.location.code}'
            self.assertEqual(total(inflow, 'quantity') - total(outflow, 'quantity'), batch.quantity, where)
            if product.is_weighed:
                self.assertEqual(total(inflow, 'weight_kg') - total(outflow, 'weight_kg'), batch.weight_kg, where)
        # And everything that left the warehouse arrived at DISPATCH.
        out = StockMovement.objects.filter(batch__product=product, batch__location=self.warehouse,
                                           movement_type='outbound', destination_location=self.dispatch)
        arrived = StockMovement.objects.filter(batch__product=product, batch__location=self.dispatch,
                                               movement_type='inbound')
        self.assertEqual(total(out, 'quantity'), total(arrived, 'quantity'))
        if product.is_weighed:
            self.assertEqual(total(out, 'weight_kg'), total(arrived, 'weight_kg'))

    def test_dispatch_location(self):
        self.assertEqual((self.dispatch.type, self.dispatch.code, self.dispatch.sellable), ('dispatch', 'DISPATCH',
                                                                                             False))
        self.assertEqual(dispatch_location(self.company), self.dispatch)
        self.assertFalse(self.dispatch.is_virtual)

    def test_each(self):
        product = ProductFactory(company=self.company, stock_quantity=0)
        batch = self.batch(product, 10)
        lot = self.pick(batch, quantity=4, weight_kg=None)
        batch.refresh_from_db()
        self.assertEqual((batch.quantity, lot.quantity, lot.location, lot.batch_number), (6, 4, self.dispatch,
                                                                                         'LOT-1'))
        self.assertEqual(lot.expiration_date, batch.expiration_date)
        self.assert_balances(product)

    def test_loose_heavier_than_recorded_books_the_gap_from_adjustment(self):
        product = ProductFactory(company=self.company, stock_quantity=0, sold_by=Product.WEIGHT,
                                 price_per_kg=D('9.99'))
        batch = self.batch(product, kg=D('50.000'))
        lot = self.pick(batch, quantity=0, weight_kg=D('51.200'))
        batch.refresh_from_db()
        self.assertEqual((batch.weight_kg, lot.weight_kg), (D('0.000'), D('51.200')))
        adjustment = virtual_location(self.company, 'adjustment')
        self.assertEqual(StockMovement.objects.get(source_location=adjustment).weight_kg, D('1.200'))
        self.assert_balances(product)

    def test_catch_weight_gap_in_and_leftover_out(self):
        product = ProductFactory(company=self.company, stock_quantity=0, sold_by=Product.WEIGHT,
                                 is_catch_weight=True, price_per_kg=D('9.99'), nominal_weight_kg=D('1.500'))
        batch = self.batch(product, 3, kg=D('4.400'))
        self.pick(batch, quantity=2, weight_kg=D('3.100'))  # 1.300 kg recorded for the last piece
        batch.refresh_from_db()
        self.assertEqual((batch.quantity, batch.weight_kg), (1, D('1.300')))
        self.pick(batch, quantity=1, weight_kg=D('1.000'))  # the last piece: 0.300 kg left goes to ADJUSTMENT
        batch.refresh_from_db()
        self.assertEqual((batch.quantity, batch.weight_kg), (0, D('0.000')))
        self.assertEqual(self.kg_at(self.dispatch, product), D('4.100'))
        heavy = self.batch(product, 1, kg=D('1.000'), number='LOT-2')
        self.pick(heavy, quantity=1, weight_kg=D('1.250'))  # heavier than recorded: 0.250 kg from ADJUSTMENT
        self.assert_balances(product)

    def test_never_takes_another_orders_reservation_or_more_than_free(self):
        from wholesale.models import SalesOrder, SalesOrderLine, TradeCustomer

        product = ProductFactory(company=self.company, stock_quantity=0)
        batch = self.batch(product, 5)
        customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                legal_name='E2E Fake Other Ltd')
        order = SalesOrder.objects.create(company=self.company, number=1, customer=customer,
                                          delivery_date=timezone.localdate(), line1='1 Fake St', town='T',
                                          postcode='TT1 1TT', status='confirmed')
        line = SalesOrderLine.objects.create(order=order, product=product, quantity=3)
        StockReservation.objects.create(batch=batch, order_line=line, quantity=3)
        with self.assertRaises(LedgerError):
            self.pick(batch, quantity=3, weight_kg=None)
        self.pick(batch, quantity=2, weight_kg=None)
        self.assertEqual(StockReservation.objects.get().quantity, 3)
        with self.assertRaises(LedgerError):
            self.pick(batch, quantity=1, weight_kg=None)

    def test_refuses_an_expired_lot_or_another_companys_location(self):
        product = ProductFactory(company=self.company, stock_quantity=0)
        batch = self.batch(product, 5)
        StockBatch.objects.filter(pk=batch.pk).update(expiration_date=timezone.localdate() - timedelta(days=1))
        with self.assertRaises(LedgerError):
            self.pick(batch, quantity=1, weight_kg=None)
        StockBatch.objects.filter(pk=batch.pk).update(expiration_date=timezone.localdate() + timedelta(days=1))
        with transaction.atomic(), self.assertRaises(LedgerError):
            pick_batch(StockBatch.objects.select_for_update().get(pk=batch.pk), quantity=1, weight_kg=None,
                       to=dispatch_location(CompanyFactory()))
        self.assertFalse(StockMovement.objects.filter(~Q(source_location__type='supplier')).exists())
