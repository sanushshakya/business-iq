# pricing/tests.py

from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, ShopifyConnectionFactory, UserFactory
from pricing.models import PriceChangeLog
from pricing.tasks import sync_approved_prices


class PriceChangeApiTests(TestCase):
    def test_approve(self):
        client = APIClient()
        client.force_authenticate(UserFactory())
        log = PriceChangeLog.objects.create(product=ProductFactory(), old_price=10, new_price=9)
        self.assertFalse(log.is_approved)
        response = client.post(reverse('pricechangelog-approve', args=[log.pk]))
        self.assertEqual(response.status_code, 200)
        log.refresh_from_db()
        self.assertTrue(log.is_approved)


class SyncApprovedPricesTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.connection = ShopifyConnectionFactory(company=self.company)

    def make_log(self, **product_kwargs):
        product = ProductFactory(**product_kwargs)
        return PriceChangeLog.objects.create(product=product, old_price=10, new_price=9, is_approved=True)

    @mock.patch('pricing.tasks.ShopifyService')
    def test_pushes_approved_price_and_marks_processed(self, service_cls):
        log = self.make_log(company=self.company, shopify_product_id=555)
        self.assertEqual(sync_approved_prices(), 1)
        service_cls.assert_called_once_with(self.connection.shop_domain)
        service_cls.return_value.update_product_price.assert_called_once_with(555, Decimal('9.00'))
        log.refresh_from_db()
        self.assertTrue(log.is_processed)

    @mock.patch('pricing.tasks.ShopifyService')
    def test_skips_products_without_shopify_link(self, service_cls):
        log = self.make_log()
        self.assertEqual(sync_approved_prices(), 0)
        service_cls.assert_not_called()
        log.refresh_from_db()
        self.assertFalse(log.is_processed)

    @mock.patch('pricing.tasks.ShopifyService')
    def test_failure_leaves_log_unprocessed(self, service_cls):
        service_cls.return_value.update_product_price.side_effect = RuntimeError('boom')
        log = self.make_log(company=self.company, shopify_product_id=1)
        self.assertEqual(sync_approved_prices(), 0)
        log.refresh_from_db()
        self.assertFalse(log.is_processed)

    @mock.patch('pricing.tasks.ShopifyService')
    def test_unapproved_and_processed_logs_are_ignored(self, service_cls):
        product = ProductFactory(company=self.company, shopify_product_id=1)
        PriceChangeLog.objects.create(product=product, old_price=10, new_price=9, is_approved=False)
        PriceChangeLog.objects.create(product=product, old_price=10, new_price=9, is_approved=True, is_processed=True)
        self.assertEqual(sync_approved_prices(), 0)
        service_cls.assert_not_called()
