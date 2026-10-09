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

from .factories import ProductFactory, StockBatchFactory


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
        self.assertEqual(self.service.calculate_recommended_price(10, 25), Decimal('12.50'))
        self.assertEqual(self.service.calculate_recommended_price(Decimal('10.00'), Decimal('12.5')), Decimal('11.25'))
        self.assertEqual(self.service.calculate_recommended_price(0.1, 10), Decimal('0.11'))  # no float error

    def test_rejects_non_positive_inputs(self):
        with self.assertRaises(ValueError):
            self.service.calculate_recommended_price(0, 25)
        with self.assertRaises(ValueError):
            self.service.calculate_recommended_price(10, 0)

    def _batch(self, days_total, days_left, product=None, **kwargs):
        batch = StockBatchFactory(
            expiration_date=timezone.localdate() + timedelta(days=days_left), **({'product': product} if product else {}), **kwargs)
        StockBatch.objects.filter(pk=batch.pk).update(receive_date=timezone.now() - timedelta(days=days_total - days_left))
        batch.refresh_from_db()
        return batch

    def approve(self, log):
        """What the approve endpoint does: the product takes the price and the change is recorded as approved."""
        log.product.price = log.new_price
        log.product.save()
        log.is_approved = True
        log.save()

    def test_a_markdown_is_proposed_not_applied(self):
        batch = self._batch(days_total=100, days_left=10)  # 10% of shelf life left -> 20% off
        self.assertEqual(self.service.propose_decay_markdowns(), 1)
        log = PriceChangeLog.objects.get()
        self.assertEqual((log.stock_batch, log.old_price, log.new_price, log.reason, log.is_approved, log.is_processed),
                         (batch, Decimal('100.00'), Decimal('80.00'), 'decay_markdown', False, False))
        batch.product.refresh_from_db()
        self.assertEqual(batch.product.price, Decimal('100.00'))  # nothing changes until staff approve

    def test_fresh_stock_is_left_alone(self):
        self._batch(days_total=100, days_left=90)
        self.assertEqual(self.service.propose_decay_markdowns(), 0)
        self.assertFalse(PriceChangeLog.objects.exists())

    def test_running_again_changes_nothing(self):
        self._batch(days_total=100, days_left=40)  # 15% off
        self.service.propose_decay_markdowns()
        self.assertEqual(self.service.propose_decay_markdowns(), 0)
        self.assertEqual(self.service.propose_decay_markdowns(), 0)
        self.assertEqual(PriceChangeLog.objects.count(), 1)

    def test_a_pending_proposal_is_updated_when_the_batch_ages_into_a_deeper_tier(self):
        batch = self._batch(days_total=100, days_left=40)
        self.service.propose_decay_markdowns()
        StockBatch.objects.filter(pk=batch.pk).update(expiration_date=timezone.localdate() + timedelta(days=10))
        self.assertEqual(self.service.propose_decay_markdowns(), 1)
        log = PriceChangeLog.objects.get()  # still one row, now the deeper markdown
        self.assertEqual((log.old_price, log.new_price), (Decimal('100.00'), Decimal('80.00')))

    def test_after_approval_a_deeper_tier_is_worked_out_from_the_original_price(self):
        batch = self._batch(days_total=100, days_left=40)
        self.service.propose_decay_markdowns()
        self.approve(PriceChangeLog.objects.get())                 # 100 -> 85 is now live
        self.assertEqual(self.service.propose_decay_markdowns(), 0)  # same tier: nothing more to say

        StockBatch.objects.filter(pk=batch.pk).update(expiration_date=timezone.localdate() + timedelta(days=10))
        self.service.propose_decay_markdowns()
        pending = PriceChangeLog.objects.get(is_approved=False)
        self.assertEqual((pending.old_price, pending.new_price), (Decimal('85.00'), Decimal('80.00')))  # 20% off 100, not off 85

    def test_a_manual_price_change_becomes_the_new_base(self):
        batch = self._batch(days_total=100, days_left=40)
        self.service.propose_decay_markdowns()
        self.approve(PriceChangeLog.objects.get())
        batch.product.refresh_from_db()
        batch.product.price = Decimal('200.00')  # someone re-prices the product
        batch.product.save()
        self.service.propose_decay_markdowns()
        pending = PriceChangeLog.objects.get(is_approved=False)
        self.assertEqual((pending.old_price, pending.new_price), (Decimal('200.00'), Decimal('170.00')))

    def test_sold_out_or_refreshed_batches_withdraw_their_pending_proposal(self):
        batch = self._batch(days_total=100, days_left=10)
        self.service.propose_decay_markdowns()
        StockBatch.objects.filter(pk=batch.pk).update(quantity=0)
        self.assertEqual(self.service.propose_decay_markdowns(), 1)
        self.assertFalse(PriceChangeLog.objects.exists())

    def test_approved_history_is_never_withdrawn(self):
        self._batch(days_total=100, days_left=10)
        self.service.propose_decay_markdowns()
        self.approve(PriceChangeLog.objects.get())
        StockBatch.objects.update(quantity=0)
        self.service.propose_decay_markdowns()
        self.assertEqual(PriceChangeLog.objects.count(), 1)

    def test_only_the_batch_closest_to_expiry_drives_a_products_price(self):
        product = ProductFactory()
        old = self._batch(days_total=100, days_left=10, product=product)    # 20% off
        self._batch(days_total=100, days_left=40, product=product)           # would be 15% off
        self.assertEqual(self.service.propose_decay_markdowns(), 1)
        log = PriceChangeLog.objects.get()
        self.assertEqual((log.stock_batch, log.new_price), (old, Decimal('80.00')))
        # the older batch sells out: the other one takes over, and the stale proposal is replaced
        StockBatch.objects.filter(pk=old.pk).update(quantity=0)
        self.service.propose_decay_markdowns()
        self.assertEqual(PriceChangeLog.objects.get().new_price, Decimal('85.00'))

    def test_each_product_is_handled_separately(self):
        self._batch(days_total=100, days_left=10)
        self._batch(days_total=100, days_left=40)
        self.assertEqual(self.service.propose_decay_markdowns(), 2)
        self.assertEqual(sorted(PriceChangeLog.objects.values_list('new_price', flat=True)), [Decimal('80.00'), Decimal('85.00')])
