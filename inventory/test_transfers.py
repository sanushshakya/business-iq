# inventory/test_transfers.py

"""G11: dispatching, receiving and cancelling transfers, and the ledger balancing at every location."""

from datetime import timedelta

from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory import transfers
from inventory.ledger import virtual_location
from inventory.models import Location, Product, StockBatch, StockMovement, Transfer
from tenants.models import Branch


def on_hand(location, product=None):
    batches = StockBatch.objects.filter(location=location)
    if product is not None:
        batches = batches.filter(product=product)
    return batches.aggregate(total=Sum('quantity'))['total'] or 0


def ledger_balance(location):
    """What the movements say is at ``location``: Σ in − Σ out."""
    moved_in = StockMovement.objects.filter(destination_location=location, movement_type='inbound')
    moved_out = StockMovement.objects.filter(source_location=location, movement_type='outbound')
    return (moved_in.aggregate(q=Sum('quantity'))['q'] or 0) - (moved_out.aggregate(q=Sum('quantity'))['q'] or 0)


class TransferFlowTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.user = UserFactory(company=self.company, is_staff=True)
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=self.branch, type='shop_floor')
        self.wh = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse', type='warehouse')
        self.transit = virtual_location(self.company, Location.IN_TRANSIT)
        self.adjustment = virtual_location(self.company, Location.ADJUSTMENT)
        self.product = ProductFactory(company=self.company, stock_quantity=10, name='Fake rice')
        self.lot = self.stock(10, 'LOT-1')

    def stock(self, quantity, number, days=30, product=None):
        return StockBatchFactory(product=product or self.product, quantity=quantity, location=self.wh,
                                 batch_number=number, expiration_date=timezone.localdate() + timedelta(days=days))

    def draft(self, quantity=6, product=None, company=None, source=None, destination=None):
        return transfers.create_transfer(
            company=company or self.company, user=self.user, source=source or self.wh,
            destination=destination or self.shop, lines=[(product or self.product, quantity)],
        )

    def entry(self, transfer, received, reason=''):
        return [{'line': transfer.lines.get().pk, 'received_quantity': received, 'reason': reason}]

    def stock_total(self):
        return Product.objects.get(pk=self.product.pk).stock_quantity

    def assert_balanced(self):
        for location in (self.wh, self.shop, self.transit):
            self.assertEqual(ledger_balance(location), on_hand(location) - (10 if location == self.wh else 0),
                             location.code)
        self.assertFalse(StockBatch.objects.filter(quantity__lt=0).exists())

    def test_full_receipt(self):
        transfer = self.draft(6)
        self.assertEqual((transfer.number, transfer.status, StockMovement.objects.count()), (1, Transfer.DRAFT, 0))

        transfers.dispatch(transfer, self.user)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.DISPATCHED)
        self.assertEqual((on_hand(self.wh), on_hand(self.transit), on_hand(self.shop)), (4, 6, 0))
        self.assert_balanced()

        transfers.receive(transfer, self.user, self.entry(transfer, 6))
        transfer.refresh_from_db()
        self.assertEqual((transfer.status, transfer.received_by), (Transfer.RECEIVED, self.user))
        self.assertEqual((on_hand(self.wh), on_hand(self.transit), on_hand(self.shop)), (4, 0, 6))
        self.assertEqual(StockBatch.objects.get(location=self.shop).batch_number, 'LOT-1')
        self.assertEqual(transfer.lines.get().received_quantity, 6)
        self.assertEqual(self.stock_total(), 10)
        self.assertEqual(StockMovement.objects.filter(transfer=transfer).count(), 4)
        self.assert_balanced()

    def test_short_receipt_goes_to_adjustment_with_reason(self):
        transfer = self.draft(6)
        transfers.dispatch(transfer, self.user)
        transfers.receive(transfer, self.user, self.entry(transfer, 4, ' Two broken '))
        self.assertEqual((on_hand(self.shop), on_hand(self.transit)), (4, 0))
        shortfall = StockMovement.objects.get(destination_location=self.adjustment)
        self.assertEqual((shortfall.quantity, shortfall.movement_type, shortfall.source_location, shortfall.transfer),
                         (2, 'outbound', self.transit, transfer))
        self.assertEqual(self.stock_total(), 8)
        self.assertEqual(transfer.lines.get().difference_reason, 'Two broken')
        self.assert_balanced()

    def test_nothing_arrived(self):
        transfer = self.draft(6)
        transfers.dispatch(transfer, self.user)
        transfers.receive(transfer, self.user, self.entry(transfer, 0, 'Lost on the road'))
        self.assertEqual((on_hand(self.shop), on_hand(self.transit), self.stock_total()), (0, 0, 4))

    def test_short_receipt_needs_a_reason(self):
        transfer = self.draft(6)
        transfers.dispatch(transfer, self.user)
        moved = StockMovement.objects.count()
        with self.assertRaises(transfers.TransferError) as raised:
            transfers.receive(transfer, self.user, self.entry(transfer, 4, '  '))
        self.assertIn('lines', raised.exception.detail)
        transfer.refresh_from_db()
        self.assertEqual((transfer.status, StockMovement.objects.count()), (Transfer.DISPATCHED, moved))

    def test_more_than_dispatched_refused(self):
        transfer = self.draft(6)
        transfers.dispatch(transfer, self.user)
        with self.assertRaises(transfers.TransferError):
            transfers.receive(transfer, self.user, self.entry(transfer, 7, 'Extra'))
        self.assertEqual(on_hand(self.transit), 6)

    def test_receive_entries_must_match_lines(self):
        other_product = ProductFactory(company=self.company, stock_quantity=5)
        self.stock(5, 'LOT-2', product=other_product)
        transfer = transfers.create_transfer(company=self.company, user=self.user, source=self.wh,
                                             destination=self.shop, lines=[(self.product, 2), (other_product, 1)])
        transfers.dispatch(transfer, self.user)
        first, second = transfer.lines.all()
        foreign = self.draft(1).lines.get()
        for entries in (
            [{'line': first.pk, 'received_quantity': 2, 'reason': ''}],  # a line missing
            [{'line': first.pk, 'received_quantity': 2, 'reason': ''}] * 2,  # one twice, the other missing
            [{'line': first.pk, 'received_quantity': 2, 'reason': ''},
             {'line': foreign.pk, 'received_quantity': 1, 'reason': ''}],  # another transfer's line
        ):
            with self.subTest(entries=entries), self.assertRaises(transfers.TransferError):
                transfers.receive(transfer, self.user, entries)
        transfer.refresh_from_db()
        self.assertEqual((transfer.status, on_hand(self.transit)), (Transfer.DISPATCHED, 3))
        transfers.receive(transfer, self.user, [{'line': second.pk, 'received_quantity': 1, 'reason': ''},
                                                {'line': first.pk, 'received_quantity': 2, 'reason': ''}])
        self.assertEqual(on_hand(self.shop), 3)

    def test_cancel_before_dispatch(self):
        transfer = self.draft(6)
        transfers.cancel(transfer, self.user)
        transfer.refresh_from_db()
        self.assertEqual((transfer.status, transfer.cancelled_by), (Transfer.CANCELLED, self.user))
        self.assertEqual(StockMovement.objects.count(), 0)
        with self.assertRaises(transfers.TransferError):
            transfers.dispatch(transfer, self.user)
        self.assertEqual(on_hand(self.wh), 10)

    def test_cannot_cancel_after_dispatch(self):
        transfer = self.draft(6)
        transfers.dispatch(transfer, self.user)
        with self.assertRaises(transfers.TransferError):
            transfers.cancel(transfer, self.user)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.DISPATCHED)

    def test_cannot_dispatch_more_than_available(self):
        transfer = self.draft(11)
        with self.assertRaises(transfers.TransferError) as raised:
            transfers.dispatch(transfer, self.user)
        self.assertIn('only 10 available at WH-1', raised.exception.detail['lines'][0])
        transfer.refresh_from_db()
        self.assertEqual((transfer.status, StockMovement.objects.count(), on_hand(self.wh)), (Transfer.DRAFT, 0, 10))

    def test_expired_stock_is_not_dispatched(self):
        self.stock(5, 'OLD', days=-1)
        transfer = self.draft(11)
        with self.assertRaises(transfers.TransferError):
            transfers.dispatch(transfer, self.user)

    def test_dispatch_takes_earliest_expiry_first(self):
        sooner = self.stock(2, 'LOT-0', days=5)
        transfer = self.draft(3)
        transfers.dispatch(transfer, self.user)
        sooner.refresh_from_db()
        self.lot.refresh_from_db()
        self.assertEqual((sooner.quantity, self.lot.quantity), (0, 9))
        self.assertEqual(sorted(p.quantity for p in transfer.lines.get().picks.all()), [1, 2])

    def test_dispatch_twice_refused(self):
        transfer = self.draft(6)
        transfers.dispatch(transfer, self.user)
        with self.assertRaises(transfers.TransferError):
            transfers.dispatch(Transfer.objects.get(pk=transfer.pk), self.user)
        self.assertEqual((on_hand(self.wh), on_hand(self.transit)), (4, 6))

    def test_receive_twice_refused(self):
        transfer = self.draft(6)
        transfers.dispatch(transfer, self.user)
        transfers.receive(transfer, self.user, self.entry(transfer, 6))
        with self.assertRaises(transfers.TransferError):
            transfers.receive(transfer, self.user, self.entry(transfer, 6))
        self.assertEqual(on_hand(self.shop), 6)

    def test_two_transfers_same_lot_received_separately(self):
        first, second = self.draft(3), self.draft(2)
        transfers.dispatch(first, self.user)
        transfers.dispatch(second, self.user)
        self.assertEqual(StockBatch.objects.filter(location=self.transit).count(), 1)
        self.assertEqual(on_hand(self.transit), 5)
        transfers.receive(first, self.user, self.entry(first, 3))
        self.assertEqual((on_hand(self.transit), on_hand(self.shop)), (2, 3))
        transfers.receive(second, self.user, self.entry(second, 1, 'One missing'))
        self.assertEqual((on_hand(self.transit), on_hand(self.shop), self.stock_total()), (0, 4, 9))

    def test_dispatch_refused_if_source_retired(self):
        transfer = self.draft(6)
        Location.objects.filter(pk=self.wh.pk).update(is_active=False)
        with self.assertRaises(transfers.TransferError):
            transfers.dispatch(transfer, self.user)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_route_rules(self):
        other = CompanyFactory()
        foreign = Location.objects.create(company=other, code='WH-X', name='Fake other', type='warehouse')
        retired = Location.objects.create(company=self.company, code='WH-OLD', name='Fake old', type='warehouse',
                                          is_active=False)
        for source, destination in ((self.wh, self.wh), (self.wh, self.transit), (self.transit, self.shop),
                                    (self.wh, foreign), (self.wh, retired)):
            with self.subTest(source=source.code, destination=destination.code), \
                    self.assertRaises(transfers.TransferError):
                self.draft(1, source=source, destination=destination)
        with self.assertRaises(transfers.TransferError):
            self.draft(1, product=ProductFactory(company=other))
        with self.assertRaises(transfers.TransferError):
            transfers.create_transfer(company=self.company, user=self.user, source=self.wh, destination=self.shop,
                                      lines=[])

    def test_numbers_per_company(self):
        other = CompanyFactory()
        other_branch = Branch.objects.create(company=other, name='Fake Elsewhere', address='x')
        other_shop = Location.objects.get(branch=other_branch, type='shop_floor')
        other_wh = Location.objects.create(company=other, code='WH-1', name='Fake warehouse', type='warehouse')
        other_product = ProductFactory(company=other)
        numbers = [self.draft(1).number, self.draft(1).number,
                   self.draft(1, company=other, source=other_wh, destination=other_shop, product=other_product).number]
        self.assertEqual(numbers, [1, 2, 1])

    def test_replace_draft(self):
        transfer = self.draft(6)
        other_product = ProductFactory(company=self.company)
        transfers.replace_draft(transfer, source=self.shop, destination=self.wh, lines=[(other_product, 2)],
                                note='Back to the warehouse')
        transfer.refresh_from_db()
        self.assertEqual((transfer.source_location, transfer.destination_location, transfer.note),
                         (self.shop, self.wh, 'Back to the warehouse'))
        self.assertEqual([(line.product, line.quantity) for line in transfer.lines.all()], [(other_product, 2)])
        transfers.dispatch(self.draft(1), self.user)
        dispatched = Transfer.objects.filter(status=Transfer.DISPATCHED).get()
        with self.assertRaises(transfers.TransferError):
            transfers.replace_draft(dispatched, source=self.wh, destination=self.shop, lines=[(self.product, 1)])
