# pricing/tests.py

from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, ShopifyConnectionFactory, StockBatchFactory, UserFactory
from inventory.models import Supplier
from pricing.models import PriceChangeLog, SupplierInvoice
from pricing.tasks import sync_approved_prices


class PriceChangeApiTests(TestCase):
    def setUp(self):
        self.staff = UserFactory(is_staff=True)
        self.member = UserFactory(company=self.staff.company)
        self.product = ProductFactory(company=self.staff.company, price=Decimal('10.00'))
        self.client = APIClient()
        self.client.force_authenticate(self.staff)
        self.url = reverse('pricechangelog-list')

    def request_change(self, **overrides):
        return self.client.post(self.url, {'product': self.product.pk, 'new_price': '8.50', 'reason': 'clearance', **overrides})

    def log(self, **overrides):
        values = dict(product=self.product, old_price=Decimal('10.00'), new_price=Decimal('8.50'))
        values.update(overrides)
        return PriceChangeLog.objects.create(**values)

    # ---- requesting
    def test_old_price_comes_from_the_product_and_flags_cannot_be_set_by_the_client(self):
        response = self.request_change(old_price='999', is_approved=True, is_processed=True)
        self.assertEqual(response.status_code, 201, response.content)
        log = PriceChangeLog.objects.get()
        self.assertEqual((log.old_price, log.new_price, log.is_approved, log.is_processed),
                         (Decimal('10.00'), Decimal('8.50'), False, False))
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('10.00'))  # requesting changes nothing yet

    def test_validation(self):
        for payload, field in (({'new_price': '0'}, 'new_price'), ({'new_price': '-1'}, 'new_price'),
                               ({'new_price': '10.00'}, 'new_price'), ({'product': 999999}, 'product')):
            response = self.request_change(**payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn(field, response.json())

    def test_the_batch_must_belong_to_the_product(self):
        other_batch = StockBatchFactory(product__company=self.staff.company)
        response = self.request_change(stock_batch=other_batch.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn('stock_batch', response.json())

    def test_only_staff_can_request_approve_or_delete(self):
        member = APIClient()
        member.force_authenticate(self.member)
        log = self.log()
        self.assertEqual(member.get(self.url).status_code, 200)
        self.assertEqual(member.post(self.url, {'product': self.product.pk, 'new_price': '8'}).status_code, 403)
        self.assertEqual(member.post(reverse('pricechangelog-approve', args=[log.pk])).status_code, 403)
        self.assertEqual(member.delete(reverse('pricechangelog-detail', args=[log.pk])).status_code, 403)

    def test_changes_cannot_be_edited_after_the_fact(self):
        detail = reverse('pricechangelog-detail', args=[self.log().pk])
        self.assertEqual(self.client.patch(detail, {'new_price': '1.00'}).status_code, 405)
        self.assertEqual(self.client.put(detail, {'new_price': '1.00'}).status_code, 405)

    # ---- approving
    def test_approving_applies_the_price_to_the_product(self):
        log = self.log()
        response = self.client.post(reverse('pricechangelog-approve', args=[log.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['is_approved'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('8.50'))

    def test_approving_twice_is_harmless(self):
        log = self.log()
        url = reverse('pricechangelog-approve', args=[log.pk])
        self.client.post(url)
        self.assertEqual(self.client.post(url).status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('8.50'))

    def test_approval_is_refused_if_the_price_moved_since_the_request(self):
        log = self.log()
        self.product.price = Decimal('12.00')
        self.product.save()
        response = self.client.post(reverse('pricechangelog-approve', args=[log.pk]))
        self.assertEqual(response.status_code, 409)
        self.assertIn('12.00', response.json()['message'] if 'message' in response.json() else response.json()['detail'])
        log.refresh_from_db()
        self.product.refresh_from_db()
        self.assertFalse(log.is_approved)
        self.assertEqual(self.product.price, Decimal('12.00'))

    def test_approved_changes_are_a_record_pending_ones_can_be_withdrawn(self):
        pending, approved = self.log(), self.log(is_approved=True)
        self.assertEqual(self.client.delete(reverse('pricechangelog-detail', args=[approved.pk])).status_code, 409)
        self.assertEqual(self.client.delete(reverse('pricechangelog-detail', args=[pending.pk])).status_code, 204)
        self.assertEqual(PriceChangeLog.objects.count(), 1)

    def test_the_nightly_job_proposes_markdowns_for_staff_to_approve(self):
        from datetime import timedelta

        from django.utils import timezone

        from inventory.models import StockBatch
        from pricing.tasks import propose_decay_markdowns

        batch = StockBatchFactory(product=self.product, expiration_date=timezone.localdate() + timedelta(days=10))
        StockBatch.objects.filter(pk=batch.pk).update(receive_date=timezone.now() - timedelta(days=90))
        self.assertEqual(propose_decay_markdowns(), 1)
        pending = PriceChangeLog.objects.get()
        self.assertEqual((pending.new_price, pending.is_approved), (Decimal('8.00'), False))
        self.client.post(reverse('pricechangelog-approve', args=[pending.pk]))
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('8.00'))


class SubscriptionPermissionTests(TestCase):
    def test_billing_changes_are_staff_only(self):
        from datetime import date

        from pricing.models import PricingPlan

        plan = PricingPlan.objects.create(name='Pro', price_per_month=50)
        staff = UserFactory(is_staff=True)
        member = UserFactory(company=staff.company)
        payload = {'user': member.pk, 'plan': plan.pk, 'start_date': date.today().isoformat()}
        as_member, as_staff = APIClient(), APIClient()
        as_member.force_authenticate(member)
        as_staff.force_authenticate(staff)
        self.assertEqual(as_member.post(reverse('subscription-list'), payload).status_code, 403)
        self.assertEqual(as_staff.post(reverse('subscription-list'), payload).status_code, 201)
        self.assertEqual(as_member.get(reverse('subscription-list')).status_code, 200)


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


@override_settings(DEFAULT_CUSTOMS_DUTY_RATE=0.0)
class PriceRecommendationApiTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.user = UserFactory()
        self.product = ProductFactory(company=self.user.company, price=Decimal('100.00'), commodity_code='0804100099')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse('price-recommendation')

    def tariff(self, rate=Decimal('6.0'), source='third_country', error=None):
        from common.services.hmrctariff_service import TariffDuty, TariffLookupError

        patcher = mock.patch('common.services.hmrctariff_service.HMRCTariffService.get_duty')
        get_duty = patcher.start()
        self.addCleanup(patcher.stop)
        if error:
            get_duty.side_effect = TariffLookupError(error)
        else:
            get_duty.return_value = TariffDuty('0804100099', 'Other', rate, f'{rate} %', source, 'IN' if source == 'preference' else None)
        return get_duty

    def test_duty_and_markup_are_worked_out_from_the_real_tariff(self):
        get_duty = self.tariff()
        body = self.client.post(self.url, {'product': self.product.pk, 'quantity': 10}).json()
        self.assertEqual((body['goods_cost'], body['duty_rate_percent'], body['duty_amount'], body['landed_cost_total']),
                         ('1000.00', '6.00', '60.00', '1060.00'))
        self.assertEqual((body['landed_cost_per_unit'], body['margin_percent'], body['recommended_unit_price']), ('106.00', '30.00', '137.80'))
        self.assertEqual((body['duty_source'], body['note'], body['current_unit_price']), ('tariff', '', '100.00'))
        get_duty.assert_called_once_with('0804100099', origin=None)

    def test_origin_applies_a_trade_deal_rate(self):
        get_duty = self.tariff(rate=Decimal('0.0'), source='preference')
        body = self.client.post(self.url, {'product': self.product.pk, 'origin': 'in'}).json()
        get_duty.assert_called_once_with('0804100099', origin='IN')
        self.assertEqual((body['duty_source'], body['duty_amount'], body['recommended_unit_price']), ('tariff_preference', '0.00', '130.00'))

    def test_margin_can_be_given_or_comes_from_the_company_setting(self):
        from common.models import Setting

        self.tariff(rate=Decimal('0.0'))
        explicit = self.client.post(self.url, {'product': self.product.pk, 'margin_percent': '50'}).json()
        self.assertEqual((explicit['margin_percent'], explicit['recommended_unit_price']), ('50.00', '150.00'))
        Setting.objects.create(company=self.user.company, key='default_margin_percent', value='40')
        configured = self.client.post(self.url, {'product': self.product.pk}).json()
        self.assertEqual((configured['margin_percent'], configured['recommended_unit_price']), ('40.00', '140.00'))

    def test_when_the_tariff_is_unavailable_the_answer_says_it_is_an_estimate(self):
        self.tariff(error='service down')
        body = self.client.post(self.url, {'product': self.product.pk}).json()
        self.assertEqual((body['duty_source'], body['note'], body['duty_amount']), ('default', 'service down', '0.00'))

    def test_a_product_without_a_commodity_code_uses_the_default_rate(self):
        self.product.commodity_code = ''
        self.product.save()
        body = self.client.post(self.url, {'product': self.product.pk}).json()
        self.assertEqual((body['duty_source'], body['note']), ('default', 'Product has no commodity code.'))

    def test_bad_input(self):
        for payload, field in (({'quantity': 0}, 'quantity'), ({'margin_percent': 0}, 'margin_percent'),
                               ({'margin_percent': 5000}, 'margin_percent'), ({'origin': 'ABC'}, 'origin'), ({'product': 999999}, 'product')):
            response = self.client.post(self.url, {'product': self.product.pk, **payload})
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn(field, response.json())
        self.assertIn('product', self.client.post(self.url, {}).json())

    def test_other_companies_products_cannot_be_priced(self):
        theirs = ProductFactory(company=CompanyFactory())
        response = self.client.post(self.url, {'product': theirs.pk})
        self.assertEqual(response.status_code, 400)
        self.assertIn('product', response.json())

    def test_requires_a_signed_in_user_with_a_company(self):
        self.assertEqual(APIClient().post(self.url, {'product': self.product.pk}).status_code, 401)
        homeless = APIClient()
        homeless.force_authenticate(UserFactory(company=None))
        self.assertEqual(homeless.post(self.url, {'product': self.product.pk}).status_code, 403)


class InvoiceFileTests(TestCase):
    PDF = b'%PDF-1.4\n%fake invoice\n'
    PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 20
    JPEG = b'\xff\xd8\xff\xe0' + b'0' * 20

    def setUp(self):
        import tempfile

        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        overrides = override_settings(MEDIA_ROOT=self.media.name)
        overrides.enable()
        self.addCleanup(overrides.disable)

        self.user = UserFactory()
        self.supplier = Supplier.objects.create(company=self.user.company, name='Acme', contact_info='c', address='a')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def upload(self, content=None, name='invoice.pdf', number='INV-1', client=None):
        from django.core.files.uploadedfile import SimpleUploadedFile

        data = {'invoice_number': number, 'supplier': self.supplier.pk, 'total_amount': '10.00', 'issued_date': '2026-10-01'}
        if content is not None:
            data['file'] = SimpleUploadedFile(name, content)
        return (client or self.client).post(reverse('supplierinvoice-list'), data, format='multipart')

    def test_upload_and_authenticated_download(self):
        response = self.upload(self.PDF, 'my scan (final).pdf')
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertNotIn('file', body)                       # the storage path is never exposed
        self.assertTrue(body['file_url'].endswith(f"/pricing/invoices/{body['id']}/file/"))

        download = self.client.get(reverse('supplierinvoice-file', args=[body['id']]))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b''.join(download.streaming_content), self.PDF)
        self.assertIn('attachment', download['Content-Disposition'])
        self.assertIn('invoice-INV-1.pdf', download['Content-Disposition'])

    def test_files_are_stored_under_a_random_name_in_the_companys_folder(self):
        invoice = SupplierInvoice.objects.get(pk=self.upload(self.PDF, '../../etc/passwd.pdf').json()['id'])
        self.assertRegex(invoice.file.name, rf'^invoices/{self.user.company.pk}/[0-9a-f]{{32}}\.pdf$')

    def test_png_and_jpeg_are_accepted(self):
        self.assertEqual(self.upload(self.PNG, 'a.png', number='A').status_code, 201)
        self.assertEqual(self.upload(self.JPEG, 'b.JPG', number='B').status_code, 201)

    def test_unsupported_or_disguised_files_are_rejected(self):
        for content, name in ((b'MZ\x90\x00 an exe', 'invoice.exe'), (b'<script>alert(1)</script>', 'invoice.html'),
                              (b'<script>alert(1)</script>', 'invoice.pdf'), (self.PNG, 'invoice.pdf'), (b'no extension', 'invoice')):
            response = self.upload(content, name)
            self.assertEqual(response.status_code, 400, name)
            self.assertIn('file', response.json())
        self.assertFalse(SupplierInvoice.objects.exists())

    def test_empty_and_oversized_files_are_rejected(self):
        self.assertEqual(self.upload(b'', 'empty.pdf').status_code, 400)
        with override_settings(INVOICE_MAX_UPLOAD_BYTES=10):
            self.assertEqual(self.upload(self.PDF, 'big.pdf').status_code, 400)

    def test_an_invoice_does_not_need_a_file(self):
        body = self.upload(None).json()
        self.assertIsNone(body['file_url'])
        self.assertEqual(self.client.get(reverse('supplierinvoice-file', args=[body['id']])).status_code, 404)

    def test_other_companies_cannot_download_it(self):
        invoice_id = self.upload(self.PDF).json()['id']
        outsider = APIClient()
        outsider.force_authenticate(UserFactory())
        self.assertEqual(outsider.get(reverse('supplierinvoice-file', args=[invoice_id])).status_code, 404)
        self.assertIn(APIClient().get(reverse('supplierinvoice-file', args=[invoice_id])).status_code, (401, 403))

    def test_deleting_the_invoice_removes_the_file(self):
        import os

        invoice = SupplierInvoice.objects.get(pk=self.upload(self.PDF).json()['id'])
        path = invoice.file.path
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.client.delete(reverse('supplierinvoice-detail', args=[invoice.pk])).status_code, 204)
        self.assertFalse(os.path.exists(path))
