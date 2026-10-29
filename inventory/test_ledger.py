# inventory/test_ledger.py

from datetime import timedelta

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from common.tests.factories import ProductFactory, StockBatchFactory, default_stock_location
from inventory.ledger import take_fefo as _take_fefo
from inventory.ledger import virtual_location
from inventory.models import StockMovement


def take_fefo(product, quantity, **kwargs):
    """FEFO over the product's default test location, selling to the customer (the pre-G09 behaviour)."""
    return _take_fefo(product, quantity, locations=[default_stock_location(product.company)],
                      destination=virtual_location(product.company, 'customer'), **kwargs)


class TakeFefoTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.product = ProductFactory(stock_quantity=0)

    def batch(self, quantity, days):
        return StockBatchFactory(product=self.product, quantity=quantity, expiration_date=self.today + timedelta(days=days))

    def test_take_fefo_oldest_expiry_first(self):
        late = self.batch(5, 20)
        early = self.batch(3, 5)
        with transaction.atomic():
            allocations, shortfall = take_fefo(self.product, 4, today=self.today)
        self.assertEqual(shortfall, 0)
        self.assertEqual([(b.pk, n) for b, n in allocations], [(early.pk, 3), (late.pk, 1)])
        early.refresh_from_db()
        late.refresh_from_db()
        self.assertEqual((early.quantity, late.quantity), (0, 4))
        self.assertEqual(StockMovement.objects.filter(movement_type='outbound').count(), 2)

    def test_take_fefo_skips_expired(self):
        expired = self.batch(10, -1)
        fresh = self.batch(2, 10)
        with transaction.atomic():
            allocations, shortfall = take_fefo(self.product, 2, today=self.today)
        self.assertEqual([(b.pk, n) for b, n in allocations], [(fresh.pk, 2)])
        self.assertEqual(shortfall, 0)
        expired.refresh_from_db()
        self.assertEqual(expired.quantity, 10)

    def test_take_fefo_returns_shortfall(self):
        self.batch(2, 10)
        with transaction.atomic():
            allocations, shortfall = take_fefo(self.product, 5, today=self.today)
        self.assertEqual(sum(n for _, n in allocations), 2)
        self.assertEqual(shortfall, 3)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, -2)  # factory batches don't add to the counter; only moves do
