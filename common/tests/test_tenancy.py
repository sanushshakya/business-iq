# common/tests/test_tenancy.py

"""Company isolation: one company must never see, change, or reference another's data."""

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.models import DemandAlert, StockAlert
from common.tenancy import TENANT_LOOKUPS, scope_queryset
from inventory.models import Product, Supplier
from logistics.models import FreightAlert, LogisticProvider
from pricing.models import PriceChangeLog, SupplierInvoice
from sync.models import ShopifyConnection, SyncTask

from .factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory


class TenancyTestCase(TestCase):
    def setUp(self):
        self.company_a = CompanyFactory()
        self.company_b = CompanyFactory()
        self.user_a = UserFactory(company=self.company_a)
        self.user_b = UserFactory(company=self.company_b)
        self.client_a = APIClient()
        self.client_a.force_authenticate(self.user_a)
        self.client_b = APIClient()
        self.client_b.force_authenticate(self.user_b)


class ListAndDetailIsolationTests(TenancyTestCase):
    def test_products_are_listed_per_company(self):
        mine = ProductFactory(company=self.company_a)
        ProductFactory(company=self.company_b)
        ids = [p['id'] for p in self.client_a.get(reverse('product-list')).json()['results']]
        self.assertEqual(ids, [mine.pk])

    def test_other_companys_object_is_404_for_every_verb(self):
        theirs = ProductFactory(company=self.company_b)
        url = reverse('product-detail', args=[theirs.pk])
        self.assertEqual(self.client_a.get(url).status_code, 404)
        self.assertEqual(self.client_a.patch(url, {'name': 'hijack'}).status_code, 404)
        self.assertEqual(self.client_a.delete(url).status_code, 404)
        theirs.refresh_from_db()
        self.assertNotEqual(theirs.name, 'hijack')

    def test_indirect_ownership_via_product(self):
        theirs = StockBatchFactory(product__company=self.company_b)
        mine = StockBatchFactory(product__company=self.company_a)
        ids = [b['id'] for b in self.client_a.get(reverse('stockbatch-list')).json()['results']]
        self.assertEqual(ids, [mine.pk])
        self.assertEqual(self.client_a.get(reverse('stockbatch-detail', args=[theirs.pk])).status_code, 404)

    def test_custom_actions_are_scoped(self):
        alert = FreightAlert.objects.create(
            company=self.company_b, shipping_lane='X', current_rate=2, baseline_rate=1, change_percent=100
        )
        self.assertEqual(self.client_a.post(reverse('freightalert-dismiss', args=[alert.pk])).status_code, 404)
        log = PriceChangeLog.objects.create(
            product=ProductFactory(company=self.company_b), old_price=2, new_price=1
        )
        self.assertEqual(self.client_a.post(reverse('pricechangelog-approve', args=[log.pk])).status_code, 404)
        log.refresh_from_db()
        self.assertFalse(log.is_approved)

    def test_common_alert_endpoints_are_scoped(self):
        StockAlert.objects.create(product=ProductFactory(company=self.company_b), current_qty=1, threshold=5)
        mine = StockAlert.objects.create(product=ProductFactory(company=self.company_a), current_qty=1, threshold=5)
        ids = [a['id'] for a in self.client_a.get(reverse('stock-alert-list')).json()['results']]
        self.assertEqual(ids, [mine.pk])

        theirs = DemandAlert.objects.create(company=self.company_b, product='P', branch='B', requested_qty=1)
        response = self.client_a.post(reverse('demand-alert-dismiss', args=[theirs.pk]))
        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_handled)

    def test_sync_models_are_scoped(self):
        SyncTask.objects.create(company=self.company_b, task_id='t', source_system='a', target_system='b')
        ShopifyConnection.objects.create(company=self.company_b, shop_domain='b.myshopify.com', access_token='x')
        self.assertEqual(self.client_a.get(reverse('synctask-list')).json()['results'], [])
        self.assertEqual(self.client_a.get(reverse('shopifyconnection-list')).json()['results'], [])


class CreateAndWriteIsolationTests(TenancyTestCase):
    def test_company_is_taken_from_the_user_not_the_payload(self):
        response = self.client_a.post(
            reverse('supplier-list'),
            {'name': 'S', 'contact_info': 'c', 'address': 'a', 'company': self.company_b.pk},
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Supplier.objects.get().company, self.company_a)

    def test_company_cannot_be_changed_on_update(self):
        product = ProductFactory(company=self.company_a)
        self.client_a.patch(reverse('product-detail', args=[product.pk]), {'company': self.company_b.pk})
        product.refresh_from_db()
        self.assertEqual(product.company, self.company_a)

    def test_cannot_reference_another_companys_object(self):
        theirs = ProductFactory(company=self.company_b)
        response = self.client_a.post(
            reverse('stockbatch-list'),
            {
                'product': theirs.pk,
                'batch_number': 'B1',
                'quantity': 1,
                'expiration_date': (date.today() + timedelta(days=5)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('product', response.json())

    def test_cannot_attach_child_to_another_companys_parent(self):
        provider = LogisticProvider.objects.create(company=self.company_b, name='P', address='a', phone_number='1')
        response = self.client_a.post(
            reverse('delivery-list'),
            {'provider': provider.pk, 'shipment_date': date.today().isoformat(), 'status': 'Pending'},
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_move_own_object_under_another_companys_parent(self):
        invoice_b = SupplierInvoice.objects.create(
            company=self.company_b, supplier=self.company_b, invoice_number='B1',
            total_amount=1, issued_date=date.today(),
        )
        response = self.client_a.post(
            reverse('invoicelineitem-list'),
            {'invoice': invoice_b.pk, 'description': 'x', 'quantity': 1, 'price_per_unit': 1, 'total_price': 1},
        )
        self.assertEqual(response.status_code, 400)

    def test_created_rows_are_visible_to_creator_only(self):
        self.client_a.post(
            reverse('product-list'), {'name': 'Mine', 'description': 'd', 'price': '1.00', 'stock_quantity': 1}
        )
        self.assertEqual(Product.objects.get().company, self.company_a)
        self.assertEqual(self.client_b.get(reverse('product-list')).json()['results'], [])


class AccessRuleTests(TenancyTestCase):
    def test_user_without_company_is_denied(self):
        orphan = APIClient()
        orphan.force_authenticate(UserFactory(company=None))
        self.assertEqual(orphan.get(reverse('product-list')).status_code, 403)
        self.assertEqual(orphan.get(reverse('stock-alert-list')).status_code, 403)

    def test_superuser_sees_everything(self):
        ProductFactory(company=self.company_a)
        ProductFactory(company=self.company_b)
        root = APIClient()
        root.force_authenticate(UserFactory(company=None, is_superuser=True, is_staff=True))
        self.assertEqual(len(root.get(reverse('product-list')).json()['results']), 2)

    def test_shared_reference_data_is_read_only_for_tenants(self):
        for name in ('category-list', 'pricingplan-list', 'culturalevent-list', 'event-list'):
            self.assertEqual(self.client_a.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client_a.post(reverse('category-list'), {'name': 'X'}).status_code, 403)

        staff = APIClient()
        staff.force_authenticate(UserFactory(is_staff=True))
        self.assertEqual(staff.post(reverse('category-list'), {'name': 'X'}).status_code, 201)

    def test_every_scoped_model_filters_correctly(self):
        # Guard against a registry entry pointing at a lookup that does not exist.
        from django.apps import apps

        for label, lookup in TENANT_LOOKUPS.items():
            model = apps.get_model(label)
            scope_queryset(model.objects.all(), self.user_a).count()  # raises FieldError if wrong
            self.assertTrue(lookup.endswith('company'), label)


class AdminScopingTests(TenancyTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Permission

        self.staff = UserFactory(company=self.company_a, is_staff=True)
        self.staff.user_permissions.set(
            Permission.objects.filter(content_type__app_label__in=['inventory', 'tenants', 'logistics'])
        )
        self.web = self.client_class()
        self.web.force_login(self.staff)

    def test_changelist_only_shows_own_company(self):
        mine = ProductFactory(company=self.company_a, name='visible-product')
        ProductFactory(company=self.company_b, name='hidden-product')
        body = self.web.get(reverse('admin:inventory_product_changelist')).content.decode()
        self.assertIn('visible-product', body)
        self.assertNotIn('hidden-product', body)
        self.assertEqual(self.web.get(reverse('admin:inventory_product_change', args=[mine.pk])).status_code, 200)

    def test_other_companys_object_is_not_reachable(self):
        theirs = ProductFactory(company=self.company_b)
        response = self.web.get(reverse('admin:inventory_product_change', args=[theirs.pk]), follow=True)
        self.assertNotContains(response, theirs.name)
        self.assertEqual(self.web.post(reverse('admin:inventory_product_delete', args=[theirs.pk]), {'post': 'yes'}).status_code, 302)
        self.assertTrue(Product.objects.filter(pk=theirs.pk).exists())

    def test_company_field_hidden_and_filled_on_create(self):
        url = reverse('admin:inventory_product_add')
        self.assertNotIn('name="company"', self.web.get(url).content.decode())
        response = self.web.post(url, {'name': 'New', 'description': 'd', 'price': '1.00', 'stock_quantity': 1, 'reorder_threshold': 5})
        self.assertEqual(response.status_code, 302, getattr(response, 'content', b'')[:300])
        self.assertEqual(Product.objects.get(name='New').company, self.company_a)

    def test_foreign_key_choices_are_limited(self):
        ProductFactory(company=self.company_b, name='other-co-product')
        ProductFactory(company=self.company_a, name='my-co-product')
        body = self.web.get(reverse('admin:inventory_stockbatch_add')).content.decode()
        self.assertIn('my-co-product', body)
        self.assertNotIn('other-co-product', body)

    def test_company_admin_shows_only_own_company_and_cannot_add(self):
        body = self.web.get(reverse('admin:tenants_company_changelist')).content.decode()
        self.assertIn(self.company_a.name, body)
        self.assertNotIn(self.company_b.name, body)
        self.assertEqual(self.web.get(reverse('admin:tenants_company_add')).status_code, 403)

    def test_auto_registered_admin_is_scoped(self):
        LogisticProvider.objects.create(company=self.company_a, name='mine-provider', address='a', phone_number='1')
        LogisticProvider.objects.create(company=self.company_b, name='theirs-provider', address='a', phone_number='1')
        body = self.web.get(reverse('admin:logistics_logisticprovider_changelist')).content.decode()
        self.assertIn('mine-provider', body)
        self.assertNotIn('theirs-provider', body)
