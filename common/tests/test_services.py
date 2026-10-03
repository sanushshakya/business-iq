# common/tests/test_services.py

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from common.services.cost_calculation_service import CostCalculationService
from common.services.hijri_calendar_service import HijriCalendarService
from common.services.hmrctariff_service import HMRCTariffService
from common.services.price_recommendation_service import PriceRecommendationService, calculate_markdown_percentage
from common.services.shopify_service import ShopifyService
from common.services.verification_token_service import VerificationTokenService
from inventory.models import StockBatch
from pricing.models import PriceChangeLog

from .factories import ShopifyConnectionFactory, StockBatchFactory


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


@override_settings(DEFAULT_CUSTOMS_DUTY_RATE=0.05)
class CostCalculationServiceTests(SimpleTestCase):
    def test_landed_cost_with_known_and_default_duty(self):
        service = CostCalculationService()
        known = mock.Mock(price=Decimal('10.00'), commodity_code='123')    # 15% duty
        unknown = mock.Mock(price=Decimal('10.00'), commodity_code='000')  # default 5%
        self.assertEqual(service.calculate_landed_cost(known, 2), Decimal('23.00'))
        self.assertEqual(service.calculate_landed_cost(unknown, 2), Decimal('21.00'))


@override_settings(HMRC_API_KEY='test-key', HMRC_API_URL='https://hmrc.test')
class HMRCTariffServiceTests(SimpleTestCase):
    @mock.patch('common.services.hmrctariff_service.requests.get')
    def test_success(self, mock_get):
        mock_get.return_value.json.return_value = {'rate': 15.0}
        result = HMRCTariffService().fetch_tariff_rate('1234')
        self.assertEqual(result, {'rate': 15.0})
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], 'https://hmrc.test/tariff-rate/1234')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer test-key')

    @mock.patch('common.services.hmrctariff_service.requests.get')
    def test_http_error_is_reported(self, mock_get):
        mock_get.return_value.raise_for_status.side_effect = requests.HTTPError('404')
        self.assertEqual(HMRCTariffService().fetch_tariff_rate('5678'), {'error': '404'})

    @override_settings(HMRC_API_KEY='')
    def test_requires_api_key(self):
        with self.assertRaises(ValueError):
            HMRCTariffService()


class HijriCalendarServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @mock.patch('common.services.hijri_calendar_service.requests.get')
    def test_parses_and_caches_next_event(self, mock_get):
        mock_get.return_value.json.return_value = {
            'data': {'events': [{'date': {'hijri': {'readable': '01 January 2030'}}}]}
        }
        service = HijriCalendarService()
        first = service.get_next_event_date()
        second = service.get_next_event_date()
        self.assertEqual(str(first), '2030-01-01')
        self.assertEqual(first, second)
        self.assertEqual(mock_get.call_count, 1)

    @mock.patch('common.services.hijri_calendar_service.requests.get')
    def test_no_events(self, mock_get):
        mock_get.return_value.json.return_value = {'data': {'events': []}}
        self.assertIsNone(HijriCalendarService().get_next_event_date())

    @mock.patch('common.services.hijri_calendar_service.requests.get', side_effect=requests.ConnectionError('down'))
    def test_request_failure_returns_none(self, _):
        self.assertIsNone(HijriCalendarService().get_next_event_date())


class ShopifyServiceTests(TestCase):
    def test_unknown_domain(self):
        with self.assertRaises(ValueError):
            ShopifyService('missing.myshopify.com')

    @mock.patch('common.services.shopify_service.requests.put')
    def test_update_product_price(self, mock_put):
        conn = ShopifyConnectionFactory()
        mock_put.return_value.json.return_value = {'product': {'id': 1}}
        result = ShopifyService(conn.shop_domain).update_product_price(1, Decimal('9.99'))
        self.assertEqual(result, {'id': 1})
        _, kwargs = mock_put.call_args
        self.assertEqual(kwargs['headers']['X-Shopify-Access-Token'], 'test-token')
