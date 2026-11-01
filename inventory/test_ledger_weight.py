# inventory/test_ledger_weight.py

"""G14: the ledger moves kg for products sold by weight — FEFO by kg (loose) or by piece (catch-weight)."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory
from inventory.ledger import LedgerError, move, take_fefo, take_weighed, virtual_location
from inventory.models import Location, StockBatch, StockMovement
from tenants.models import Branch

D = Decimal


class WeightLedgerTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.company = CompanyFactory()
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=self.branch, type='shop_floor')
        self.customer = virtual_location(self.company, Location.CUSTOMER)
        self.adjustment = virtual_location(self.company, Location.ADJUSTMENT)
        self.loose = ProductFactory(company=self.company, sold_by='weight', price_per_kg=D('8.0000'),
                                    stock_quantity=0)
        self.packs = ProductFactory(company=self.company, sold_by='weight', is_catch_weight=True,
                                    price_per_kg=D('9.0000'), stock_quantity=0)

    def batch(self, product, quantity, kg, days, number):
        """A batch received through the ledger (SUPPLIER → shop), so its stock shows in the movements."""
        batch = StockBatchFactory(product=product, quantity=0, weight_kg=D('0'), batch_number=number,
                                  location=self.shop, expiration_date=self.today + timedelta(days=days))
        with transaction.atomic():
            move(batch, quantity, 'inbound', counterpart=virtual_location(self.company, Location.SUPPLIER),
                 weight_kg=D(kg) if product.is_weighed else None)
        return batch

    def take(self, product, quantity, kg):
        with transaction.atomic():
            return take_weighed(product, quantity=quantity, weight_kg=None if kg is None else D(kg),
                                locations=[self.shop], destination=self.customer, today=self.today)

    def held(self, batch):
        batch.refresh_from_db()
        return batch.quantity, batch.weight_kg

    def assert_balanced(self):
        """Kg in = kg out + kg on hand, per real location; no batch below zero."""
        def total(rows):
            return sum((row.weight_kg or D('0') for row in rows), D('0'))
        movements = StockMovement.objects.all()
        in_ = total(movements.filter(destination_location=self.shop))
        out = total(movements.filter(source_location=self.shop))
        self.assertEqual(in_ - out, total(StockBatch.objects.filter(location=self.shop)))
        self.assertFalse(StockBatch.objects.filter(weight_kg__lt=0).exists())
        self.assertFalse(StockBatch.objects.filter(quantity__lt=0).exists())

    def test_move_refuses_kg_for_each_and_needs_it_for_weighed(self):
        each = self.batch(ProductFactory(company=self.company), 5, '0', 10, 'E-1')
        loose = self.batch(self.loose, 0, '2.000', 10, 'L-1')
        with transaction.atomic():
            with self.assertRaises(LedgerError):
                move(each, 1, 'outbound', counterpart=self.customer, weight_kg=D('0.100'))
            with self.assertRaises(LedgerError):
                move(loose, 0, 'outbound', counterpart=self.customer)
            with self.assertRaises(LedgerError):  # each products only, through take_fefo
                take_fefo(self.loose, 1, locations=[self.shop], destination=self.customer)
            move(loose, 0, 'outbound', counterpart=self.customer, weight_kg=D('0.500'))
        self.assertEqual(self.held(loose), (0, D('1.500')))

    def test_loose_takes_kg_fefo(self):
        later = self.batch(self.loose, 0, '5.000', 20, 'L-2')
        sooner = self.batch(self.loose, 0, '1.200', 5, 'L-1')
        allocations = self.take(self.loose, 1, '2.000')
        self.assertEqual([(b.pk, pieces, kg) for b, pieces, kg in allocations],
                         [(sooner.pk, 0, D('1.200')), (later.pk, 0, D('0.800'))])
        self.assertEqual((self.held(sooner), self.held(later)), ((0, D('0.000')), (0, D('4.200'))))
        self.assertEqual(sum(m.weight_kg for m in StockMovement.objects.filter(destination_location=self.customer)),
                         D('2.000'))
        self.assert_balanced()

    def test_loose_short_books_the_gap_to_adjustment(self):
        batch = self.batch(self.loose, 0, '1.000', 5, 'L-1')
        allocations = self.take(self.loose, 1, '1.250')
        self.assertEqual([(pieces, kg) for _, pieces, kg in allocations], [(0, D('1.250'))])
        gap = StockMovement.objects.get(source_location=self.adjustment)
        self.assertEqual((gap.destination_location, gap.weight_kg, gap.quantity), (self.shop, D('0.250'), 0))
        self.assertEqual(self.held(batch), (0, D('0.000')))
        self.assert_balanced()

    def test_loose_only_expired_refused(self):
        expired = self.batch(self.loose, 0, '3.000', -1, 'L-1')
        with self.assertRaises(LedgerError):
            self.take(self.loose, 1, '0.500')
        self.assertEqual(self.held(expired), (0, D('3.000')))
        self.assertFalse(StockMovement.objects.filter(destination_location=self.customer).exists())

    def test_catch_weight_split_sums_exactly(self):
        first = self.batch(self.packs, 1, '0.500', 5, 'P-1')
        second = self.batch(self.packs, 5, '2.500', 10, 'P-2')
        allocations = self.take(self.packs, 3, '1.001')
        self.assertEqual([(b.pk, pieces) for b, pieces, _ in allocations], [(first.pk, 1), (second.pk, 2)])
        self.assertEqual(sum(kg for _, _, kg in allocations), D('1.001'))
        self.assertEqual([kg for _, _, kg in allocations], [D('0.334'), D('0.667')])
        self.assertEqual(self.held(second), (3, D('1.833')))
        self.assert_balanced()

    def test_catch_weight_last_piece_leaves_no_kg(self):
        batch = self.batch(self.packs, 2, '1.000', 5, 'P-1')
        self.take(self.packs, 1, '0.450')
        self.take(self.packs, 1, '0.480')  # the last piece: the 0.070 left over goes to ADJUSTMENT
        self.assertEqual(self.held(batch), (0, D('0.000')))
        leftover = StockMovement.objects.get(destination_location=self.adjustment)
        self.assertEqual((leftover.quantity, leftover.weight_kg), (0, D('0.070')))
        self.assert_balanced()

    def test_catch_weight_heavier_than_recorded_books_the_gap(self):
        batch = self.batch(self.packs, 2, '0.800', 5, 'P-1')
        self.take(self.packs, 1, '0.900')
        gap = StockMovement.objects.get(source_location=self.adjustment)
        self.assertEqual(gap.weight_kg, D('0.100'))
        self.assertEqual(self.held(batch), (1, D('0.000')))
        self.assert_balanced()

    def test_catch_weight_needs_enough_pieces(self):
        self.batch(self.packs, 1, '0.500', 5, 'P-1')
        with self.assertRaises(LedgerError) as caught:
            self.take(self.packs, 2, '1.000')
        self.assertEqual(caught.exception.available, 1)
        self.assertFalse(StockMovement.objects.filter(destination_location=self.customer).exists())

    def test_catch_weight_without_kg_takes_the_average(self):
        batch = self.batch(self.packs, 3, '1.000', 5, 'P-1')
        allocations = self.take(self.packs, 1, None)
        self.assertEqual(allocations[0][2], D('0.333'))
        self.take(self.packs, 2, None)  # the rest, with all the kg left
        self.assertEqual(self.held(batch), (0, D('0.000')))
        self.assert_balanced()


class WeightLedgerReviewTests(WeightLedgerTests):
    """G14 review: exact kg splits and a ledger floor at zero."""

    def test_catch_weight_split_never_negative(self):
        batches = [self.batch(self.packs, 1, '0.500', 5 + n, f'P-{n}') for n in range(6)]
        allocations = self.take(self.packs, 6, '0.003')
        self.assertEqual(sum(kg for _, _, kg in allocations), D('0.003'))
        self.assertTrue(all(kg >= 0 for _, _, kg in allocations))
        self.assertEqual(len(allocations), len(batches))
        self.assert_balanced()

    def test_move_refuses_to_go_below_zero(self):
        loose = self.batch(self.loose, 0, '1.000', 5, 'L-1')
        each = self.batch(ProductFactory(company=self.company), 2, '0', 5, 'E-1')
        with transaction.atomic():
            with self.assertRaises(LedgerError):
                move(loose, 0, 'outbound', counterpart=self.customer, weight_kg=D('1.001'))
            with self.assertRaises(LedgerError):
                move(each, 3, 'outbound', counterpart=self.customer)
        self.assertEqual((self.held(loose), self.held(each)), ((0, D('1.000')), (2, D('0.000'))))
