"""G57 over the API: supplier products, drafting now, editing, approving and cancelling orders; roles and tenancy."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from config.celery import app
from inventory.models import Supplier
from purchasing.models import PurchaseOrder, SupplierProduct
from purchasing.tests.test_drafting import DraftCase
from tenants.roles import STOCK

D = Decimal


class PurchasingApiTests(DraftCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.dates = self.product(rate='10', stock=40)

    def drafted(self):
        answer = self.api.post(reverse('purchaseorder-draft'), {}, format='json')
        self.assertEqual(answer.status_code, 200)
        return answer.json()

    def test_supplier_products_and_terms(self):
        rice = ProductFactory(company=self.company, name='Fake Rice')
        url = reverse('supplierproduct-list')
        made = self.api.post(url, {'supplier': self.supplier.pk, 'product': rice.pk, 'pack_size': 10,
                                   'pack_cost': '12.50', 'preferred': True}, format='json')
        self.assertEqual(made.status_code, 201)
        self.assertEqual(self.api.post(url, {'supplier': self.supplier.pk, 'product': rice.pk, 'pack_size': 5},
                                       format='json').status_code, 400)  # one link per supplier and product
        other = Supplier.objects.create(company=self.company, name='Fake Other', contact_info='-', address='-')
        self.assertEqual(self.api.post(url, {'supplier': other.pk, 'product': rice.pk, 'pack_size': 1,
                                             'preferred': True}, format='json').status_code, 400)  # one preferred
        self.assertEqual(self.api.post(url, {'supplier': other.pk, 'product': rice.pk, 'pack_size': 0},
                                       format='json').status_code, 400)
        theirs = ProductFactory(company=CompanyFactory())
        self.assertEqual(self.api.post(url, {'supplier': other.pk, 'product': theirs.pk, 'pack_size': 1},
                                       format='json').status_code, 400)
        self.assertEqual(self.member.post(url, {'supplier': other.pk, 'product': rice.pk, 'pack_size': 1},
                                          format='json').status_code, 403)
        terms = self.api.patch(reverse('supplier-detail', args=[self.supplier.pk]),
                               {'lead_time_days': 2, 'order_cycle_days': 14, 'min_order_value': '150.00'},
                               format='json')
        self.assertEqual((terms.status_code, terms.json()['min_order_value']), (200, '150.00'))

    def test_draft_edit_approve(self):
        self.assertEqual(self.member.post(reverse('purchaseorder-draft'), {}, format='json').status_code, 403)
        self.assertEqual(self.drafted()['created'], 1)
        [order] = self.member.get(reverse('purchaseorder-list')).json()['results']
        self.assertEqual((order['number_display'], order['supplier_name'], order['status']),
                         ('PO-000001', 'Fake Foods Ltd', 'draft'))
        [line] = order['lines']
        self.assertEqual((line['product_name'], line['packs'], line['units'], line['target'], line['line_cost']),
                         ('Fake Medjool Dates', 6, 72, 105, '144.00'))
        edit = reverse('purchaseorder-line', args=[order['id'], line['id']])
        self.assertEqual(self.member.patch(edit, {'packs': 4}, format='json').status_code, 403)
        self.assertEqual(self.api.patch(edit, {'packs': -1}, format='json').status_code, 400)
        edited = self.api.patch(edit, {'packs': 4}, format='json').json()
        self.assertEqual((edited['edited'], edited['total_cost'], edited['lines'][0]['units']), (True, '96.00', 48))
        self.assertEqual(self.drafted()['kept'], 1)  # a run leaves it alone
        approved = self.api.post(reverse('purchaseorder-approve', args=[order['id']]), {}, format='json')
        self.assertEqual((approved.status_code, approved.json()['status']), (200, 'approved'))
        self.assertEqual(self.api.patch(edit, {'packs': 5}, format='json').status_code, 400)  # approved: fixed
        self.assertEqual(self.api.post(reverse('purchaseorder-approve', args=[order['id']]), {},
                                       format='json').status_code, 400)
        cancelled = self.api.post(reverse('purchaseorder-cancel', args=[order['id']]), {}, format='json')
        self.assertEqual(cancelled.json()['status'], 'cancelled')
        self.assertEqual(self.api.delete(reverse('purchaseorder-detail', args=[order['id']])).status_code, 405)

    def test_filters_tenancy_and_operators(self):
        self.drafted()
        self.assertEqual(len(self.member.get(reverse('purchaseorder-list'), {'status': 'approved'}).json()['results']),
                         0)
        self.assertEqual(self.member.get(reverse('purchaseorder-list'), {'status': 'x'}).status_code, 400)
        stranger = APIClient()
        stranger.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True, is_superuser=True))
        order = PurchaseOrder.objects.get()
        self.assertEqual(stranger.get(reverse('purchaseorder-detail', args=[order.pk])).status_code, 404)
        self.assertEqual(stranger.get(reverse('supplierproduct-list')).json()['results'], [])
        self.assertEqual(stranger.post(reverse('purchaseorder-approve', args=[order.pk]), {},
                                       format='json').status_code, 404)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        picker = APIClient()
        picker.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        for url in (reverse('purchaseorder-list'), reverse('supplierproduct-list')):
            self.assertEqual(picker.get(url).status_code, 403)
        self.assertTrue(SupplierProduct.objects.exists())

    def test_a_superuser_links_only_their_own_supplier_and_product(self):
        admin = APIClient()
        admin.force_authenticate(UserFactory(company=self.company, is_staff=True, is_superuser=True))
        elsewhere = CompanyFactory()
        theirs = ProductFactory(company=elsewhere)
        their_supplier = Supplier.objects.create(company=elsewhere, name='Fake Theirs', contact_info='-', address='-')
        mine = ProductFactory(company=self.company)
        url = reverse('supplierproduct-list')
        for body in ({'supplier': self.supplier.pk, 'product': theirs.pk, 'pack_size': 1},
                     {'supplier': their_supplier.pk, 'product': mine.pk, 'pack_size': 1}):
            self.assertEqual(admin.post(url, body, format='json').status_code, 400)

    def test_members_do_not_change_supplier_terms(self):
        url = reverse('supplier-detail', args=[self.supplier.pk])
        self.member.patch(url, {'min_order_value': '999.00', 'lead_time_days': 30}, format='json')
        self.supplier.refresh_from_db()
        self.assertEqual((self.supplier.min_order_value, self.supplier.lead_time_days), (D('0.00'), 3))

    def test_approving_sets_when_it_is_due(self):
        from vat.services import uk_date
        from datetime import timedelta

        self.drafted()
        order = PurchaseOrder.objects.get()
        approved = self.api.post(reverse('purchaseorder-approve', args=[order.pk]), {}, format='json').json()
        self.assertEqual(approved['expected_on'], (uk_date() + timedelta(days=3)).isoformat())

    def test_beat_runs_the_drafting_task(self):
        import purchasing.tasks  # noqa: F401 - what the worker's autodiscovery imports

        self.assertIn(app.conf.beat_schedule['draft-purchase-orders-nightly']['task'], app.tasks)
