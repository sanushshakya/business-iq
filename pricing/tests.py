# pricing/tests.py

from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, ShopifyConnectionFactory, UserFactory
from inventory.models import Supplier
from pricing.models import PriceChangeLog, SupplierInvoice
from pricing.tasks import sync_approved_prices


class PriceChangeApiTests(TestCase):
    def test_approve(self):
        client = APIClient()
        user = UserFactory()
        client.force_authenticate(user)
        log = PriceChangeLog.objects.create(product=ProductFactory(company=user.company), old_price=10, new_price=9)
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


class SupplierInvoiceTests(TestCase):
    def setUp(self):
        self.user = UserFactory()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.supplier = Supplier.objects.create(company=self.user.company, name='Acme', contact_info='c', address='a')
        self.url = reverse('supplierinvoice-list')

    def payload(self, **overrides):
        data = {
            'invoice_number': 'INV-001', 'supplier': self.supplier.pk, 'total_amount': '10.00',
            'issued_date': '2026-10-01',
        }
        data.update(overrides)
        return data

    def test_create_stamps_the_users_company(self):
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(SupplierInvoice.objects.get().company, self.user.company)

    def test_invoice_numbers_are_unique_per_company_not_globally(self):
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 201)

        other = UserFactory()
        other_client = APIClient()
        other_client.force_authenticate(other)
        other_supplier = Supplier.objects.create(company=other.company, name='Other', contact_info='c', address='a')
        response = other_client.post(self.url, self.payload(supplier=other_supplier.pk))
        self.assertEqual(response.status_code, 201, response.content)  # same number, different company: fine

    def test_duplicate_number_within_a_company_is_a_clean_400(self):
        self.client.post(self.url, self.payload())
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 400)
        self.assertIn('invoice_number', response.json())

    def test_updating_an_invoice_does_not_clash_with_itself(self):
        invoice_id = self.client.post(self.url, self.payload()).json()['id']
        response = self.client.patch(reverse('supplierinvoice-detail', args=[invoice_id]), {'total_amount': '99.00'})
        self.assertEqual(response.status_code, 200, response.content)

    def test_supplier_must_belong_to_the_same_company(self):
        foreign = Supplier.objects.create(company=CompanyFactory(), name='Foreign', contact_info='c', address='a')
        response = self.client.post(self.url, self.payload(supplier=foreign.pk))
        self.assertEqual(response.status_code, 400)
        self.assertIn('supplier', response.json())

    def test_superuser_cannot_mix_companies_either(self):
        root = APIClient()
        root.force_authenticate(UserFactory(company=None, is_superuser=True, is_staff=True))
        other_company = CompanyFactory()
        response = root.post(self.url, self.payload(company=other_company.pk))  # supplier is the first company's
        self.assertEqual(response.status_code, 400)
        self.assertIn('supplier', response.json())

    def test_a_supplier_with_invoices_cannot_be_deleted(self):
        self.client.post(self.url, self.payload())
        response = self.client.delete(reverse('supplier-detail', args=[self.supplier.pk]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'Protected')
        self.assertTrue(Supplier.objects.filter(pk=self.supplier.pk).exists())

    def test_a_supplier_without_invoices_can_be_deleted(self):
        self.assertEqual(self.client.delete(reverse('supplier-detail', args=[self.supplier.pk])).status_code, 204)
