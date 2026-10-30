# inventory/test_transfer_ledger.py

"""G11: the ledger primitives transfers use (moving units into the same lot at another location)."""

from datetime import timedelta

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory
from inventory.ledger import LedgerError, move, transfer_batch, transfer_fefo, virtual_location
from inventory.models import Location, Product, StockBatch, StockMovement
from tenants.models import Branch


class TransferLedgerTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=self.branch, type='shop_floor')
        self.wh = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse', type='warehouse')
        self.transit = virtual_location(self.company, Location.IN_TRANSIT)
        self.product = ProductFactory(company=self.company, stock_quantity=0)

    def batch(self, quantity, days, number=None):
        batch = StockBatchFactory(product=self.product, quantity=quantity, location=self.wh,
                                  expiration_date=timezone.localdate() + timedelta(days=days),
                                  **({'batch_number': number} if number else {}))
        Product.objects.filter(pk=self.product.pk).update(stock_quantity=Product.objects.get(pk=self.product.pk).stock_quantity + quantity)
        return batch

    def test_transfer_batch_moves_into_same_lot_at_destination(self):
        source = self.batch(10, 30, 'LOT-1')
        with transaction.atomic():
            held = transfer_batch(source, 4, self.transit)
        source.refresh_from_db()
        held.refresh_from_db()
        self.assertEqual(source.quantity, 6)
        self.assertEqual((held.location, held.batch_number, held.expiration_date, held.quantity),
                         (self.transit, 'LOT-1', source.expiration_date, 4))
        out, in_ = StockMovement.objects.order_by('pk')
        self.assertEqual((out.batch, out.movement_type, out.source_location, out.destination_location),
                         (source, 'outbound', self.wh, self.transit))
        self.assertEqual((in_.batch, in_.movement_type, in_.source_location, in_.destination_location),
                         (held, 'inbound', self.wh, self.transit))
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock_quantity, 10)

    def test_transfer_batch_adds_to_an_existing_lot(self):
        source = self.batch(10, 30, 'LOT-1')
        with transaction.atomic():
            first = transfer_batch(source, 2, self.transit)
            second = transfer_batch(source, 3, self.transit)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(StockBatch.objects.get(pk=first.pk).quantity, 5)

    def test_transfer_fefo_takes_earliest_in_date_skips_expired(self):
        expired = self.batch(5, -1)
        later = self.batch(3, 10)
        sooner = self.batch(3, 5)
        with transaction.atomic():
            allocations = transfer_fefo(self.product, 4, source=self.wh, to=self.transit)
        self.assertEqual([(a.pk, taken) for a, _, taken in allocations], [(sooner.pk, 3), (later.pk, 1)])
        expired.refresh_from_db()
        self.assertEqual(expired.quantity, 5)
        self.assertEqual(sum(StockBatch.objects.filter(location=self.transit).values_list('quantity', flat=True)), 4)

    def test_transfer_fefo_only_takes_from_the_source(self):
        StockBatchFactory(product=self.product, quantity=9, location=self.shop)
        self.batch(2, 5)
        with self.assertRaises(LedgerError), transaction.atomic():
            transfer_fefo(self.product, 3, source=self.wh, to=self.transit)

    def test_transfer_fefo_short_raises_and_moves_nothing(self):
        self.batch(3, 5)
        with self.assertRaises(LedgerError) as raised, transaction.atomic():
            transfer_fefo(self.product, 5, source=self.wh, to=self.transit)
        self.assertEqual(raised.exception.available, 3)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_move_links_transfer(self):
        from inventory.models import Transfer

        transfer = Transfer.objects.create(company=self.company, number=1, source_location=self.wh,
                                           destination_location=self.shop)
        source = self.batch(4, 5)
        with transaction.atomic():
            movement = move(source, 1, 'outbound', counterpart=self.transit, transfer=transfer)
        self.assertEqual(movement.transfer, transfer)
        self.assertEqual(transfer.reference, 'T-000001')
