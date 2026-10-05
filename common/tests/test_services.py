# common/tests/test_services.py

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from common.services.price_recommendation_service import PriceRecommendationService, calculate_markdown_percentage
from common.services.verification_token_service import VerificationTokenService
from inventory.models import StockBatch
from pricing.models import PriceChangeLog

from .factories import StockBatchFactory


class VerificationTokenServiceTests(SimpleTestCase):
    def test_round_trip(self):
        token = VerificationTokenService.generate_token(7)
        self.assertEqual(VerificationTokenService.verify_token(token), 7)

    def test_tampered_token(self):
        token = VerificationTokenService.generate_token(7)
        self.assertIsNone(VerificationTokenService.verify_token(token[:-2] + 'xx'))

    def test_expired_token(self):
        token = VerificationTokenService.generate_token(7)
        with mock.patch('django.core.signing.time.time', return_value=10**11):
            self.assertIsNone(VerificationTokenService.verify_token(token))


class MarkdownTierTests(SimpleTestCase):
    def test_tiers(self):
        self.assertEqual(calculate_markdown_percentage(10), 20)
        self.assertEqual(calculate_markdown_percentage(24.9), 20)
        self.assertEqual(calculate_markdown_percentage(25), 15)
        self.assertEqual(calculate_markdown_percentage(49), 15)
        self.assertEqual(calculate_markdown_percentage(50), 0)
        self.assertEqual(calculate_markdown_percentage(100), 0)


class PriceRecommendationServiceTests(TestCase):
    def setUp(self):
        self.service = PriceRecommendationService()

    def test_recommended_price(self):
        self.assertAlmostEqual(self.service.calculate_recommended_price(10, 25), 12.5)

    def test_rejects_non_positive_inputs(self):
        with self.assertRaises(ValueError):
            self.service.calculate_recommended_price(0, 25)
        with self.assertRaises(ValueError):
            self.service.calculate_recommended_price(10, 0)

    def _batch(self, days_total, days_left):
        batch = StockBatchFactory(expiration_date=timezone.localdate() + timedelta(days=days_left))
        StockBatch.objects.filter(pk=batch.pk).update(receive_date=timezone.now() - timedelta(days=days_total - days_left))
        batch.refresh_from_db()
        return batch

    def test_markdown_applied_for_short_shelf_life(self):
        batch = self._batch(days_total=100, days_left=10)  # 10% left -> 20% off
        old, new = self.service.apply_markdown_discounts(batch)
        self.assertEqual((old, new), (Decimal('100.00'), Decimal('80.00')))
        batch.product.refresh_from_db()
        self.assertEqual(batch.product.price, Decimal('80.00'))

    def test_no_markdown_for_fresh_stock(self):
        batch = self._batch(days_total=100, days_left=90)
        old, new = self.service.apply_markdown_discounts(batch)
        self.assertEqual(old, new)

    def test_decay_pricing_logs_changes_only(self):
        stale = self._batch(days_total=100, days_left=40)  # 40% left -> 15% off
        self._batch(days_total=100, days_left=90)
        self.service.apply_decay_pricing()

        log = PriceChangeLog.objects.get()
        self.assertEqual(log.stock_batch, stale)
        self.assertEqual((log.old_price, log.new_price), (Decimal('100.00'), Decimal('85.00')))
        self.assertEqual(log.reason, 'decay_markdown')

    def test_repeated_runs_do_not_compound(self):
        batch = self._batch(days_total=100, days_left=40)  # 15% off
        self.service.apply_decay_pricing()
        self.service.apply_decay_pricing()
        self.service.apply_decay_pricing()
        batch.product.refresh_from_db()
        self.assertEqual(batch.product.price, Decimal('85.00'))
        self.assertEqual(PriceChangeLog.objects.count(), 1)

    def test_moving_to_a_deeper_tier_is_calculated_from_the_original_price(self):
        batch = self._batch(days_total=100, days_left=40)  # 15% off -> 85
        self.service.apply_decay_pricing()
        StockBatch.objects.filter(pk=batch.pk).update(expiration_date=timezone.localdate() + timedelta(days=10))
        batch.refresh_from_db()
        self.service.apply_decay_pricing()  # now 20% off the ORIGINAL 100, not off 85

        batch.product.refresh_from_db()
        self.assertEqual(batch.product.price, Decimal('80.00'))
        self.assertEqual(PriceChangeLog.objects.count(), 2)

    def test_manual_price_change_resets_the_base(self):
        batch = self._batch(days_total=100, days_left=40)
        self.service.apply_decay_pricing()  # 100 -> 85
        batch.product.price = Decimal('200.00')  # someone re-prices the product
        batch.product.save()
        self.service.apply_decay_pricing()
        batch.product.refresh_from_db()
        self.assertEqual(batch.product.price, Decimal('170.00'))

    def test_empty_batches_are_ignored(self):
        batch = self._batch(days_total=100, days_left=10)
        StockBatch.objects.filter(pk=batch.pk).update(quantity=0)
        self.service.apply_decay_pricing()
        self.assertFalse(PriceChangeLog.objects.exists())
