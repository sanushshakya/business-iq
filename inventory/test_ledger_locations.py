# inventory/test_ledger_locations.py

"""G09: the ledger works per location — balances, FEFO and deductions never cross locations."""

from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory
from inventory.models import Location, StockBatch, StockMovement
from tenants.models import Branch


class LedgerLocationTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.company = CompanyFactory()
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=self.branch, type='shop_floor')
        self.back = Location.objects.create(company=self.company, branch=self.branch, code='BACK-1',
                                            name='Fake back store', type='back_store')
        self.product = ProductFactory(company=self.company, stock_quantity=0)

    def batch(self, quantity, days, location=None, number=None):
        fields = {'product': self.product, 'quantity': quantity, 'location': location or self.shop,
                  'expiration_date': self.today + timedelta(days=days)}
        if number:
            fields['batch_number'] = number
        return StockBatchFactory(**fields)

    def virtual(self, type_):
        from inventory.ledger import virtual_location

        return virtual_location(self.company, type_)

    def test_factory_puts_batches_at_the_branch_shop_floor(self):
        self.assertEqual(StockBatchFactory(product=self.product).location, self.shop)

    def test_move_records_source_and_destination(self):
        from inventory.ledger import move

        batch = self.batch(0, 30)
        with transaction.atomic():
            move(batch, 5, 'inbound', counterpart=self.virtual('supplier'))
            move(batch, 2, 'outbound', counterpart=self.virtual('customer'))
        inbound, outbound = StockMovement.objects.order_by('pk')
        self.assertEqual((inbound.source_location.type, inbound.destination_location), ('supplier', self.shop))
        self.assertEqual((outbound.source_location, outbound.destination_location.type), (self.shop, 'customer'))
        batch.refresh_from_db()
        self.assertEqual(batch.quantity, 3)

    def test_balance_per_location(self):
        """Stock at a location = in (destination) - out (source) = the batches there."""
        from inventory.ledger import move

        supplier, customer = self.virtual('supplier'), self.virtual('customer')
        shop_batch, back_batch = self.batch(0, 30), self.batch(0, 30, location=self.back, number='LOT-1')
        with transaction.atomic():
            move(shop_batch, 10, 'inbound', counterpart=supplier)
            move(back_batch, 6, 'inbound', counterpart=supplier)
            move(shop_batch, 4, 'outbound', counterpart=customer)
            move(back_batch, 1, 'outbound', counterpart=customer)
        for location, expected in ((self.shop, 6), (self.back, 5)):
            in_ = StockMovement.objects.filter(destination_location=location).aggregate(t=Sum('quantity'))['t'] or 0
            out = StockMovement.objects.filter(source_location=location).aggregate(t=Sum('quantity'))['t'] or 0
            held = StockBatch.objects.filter(location=location).aggregate(t=Sum('quantity'))['t'] or 0
            self.assertEqual((in_ - out, held), (expected, expected), location.code)

    def test_fefo_within_locations(self):
        from inventory.ledger import take_fefo

        self.batch(5, 2, location=self.back, number='EARLY-BACK')  # earliest, but not in the allowed locations
        later = self.batch(4, 20)
        early = self.batch(3, 10)
        with transaction.atomic():
            allocations, shortfall = take_fefo(self.product, 5, locations=[self.shop],
                                               destination=self.virtual('customer'), today=self.today)
        self.assertEqual(([(b.pk, n) for b, n in allocations], shortfall), ([(early.pk, 3), (later.pk, 2)], 0))

    def test_no_cross_location_deduction(self):
        from inventory.ledger import take_fefo

        back_batch = self.batch(10, 10, location=self.back, number='BACK-LOT')
        with transaction.atomic():
            allocations, shortfall = take_fefo(self.product, 3, locations=[self.shop],
                                               destination=self.virtual('customer'), today=self.today)
        self.assertEqual((allocations, shortfall), ([], 3))
        back_batch.refresh_from_db()
        self.assertEqual(back_batch.quantity, 10)
        self.assertFalse(StockMovement.objects.exists())

    def test_fefo_tie_goes_to_oldest_receipt(self):
        from inventory.ledger import take_fefo

        first = self.batch(2, 10, number='FIRST')
        second = self.batch(2, 10, number='SECOND')
        StockBatch.objects.filter(pk=first.pk).update(receive_date=timezone.now() - timedelta(days=3))
        StockBatch.objects.filter(pk=second.pk).update(receive_date=timezone.now() - timedelta(days=5))
        with transaction.atomic():
            allocations, _ = take_fefo(self.product, 1, locations=[self.shop],
                                       destination=self.virtual('customer'), today=self.today)
        self.assertEqual(allocations[0][0].pk, second.pk)

    def test_counterpart_from_other_company_refused(self):
        from inventory.ledger import LedgerError, move, virtual_location

        theirs = virtual_location(CompanyFactory(), 'supplier')
        with self.assertRaises(LedgerError), transaction.atomic():
            move(self.batch(0, 30), 1, 'inbound', counterpart=theirs)

    def test_helpers(self):
        from inventory.ledger import online_locations, primary_location, sellable_locations

        Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse', type='warehouse')
        self.assertEqual(list(sellable_locations(self.branch)), [self.shop])
        self.assertEqual(list(online_locations(self.company)), [self.shop])
        self.assertEqual(primary_location(self.company), self.shop)
        self.assertIsNone(primary_location(CompanyFactory()))
