# sales/tests/test_catalogue.py

"""G26: the till catalogue — everything a till needs to find and price products, sent as changes since a cursor."""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from common.models import Setting
from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from inventory.models import BarcodeRule, Product
from sales.tests.test_sales import SalesTestCase
from tenants.roles import TILL

D = Decimal


class CatalogueTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.rice = self.stocked(price='2.50', name='Fake Rice', barcode='5012345678900')
        self.mince = ProductFactory(company=self.company, name='Fake Mince', price=D('0.00'), sold_by='weight',
                                    price_per_kg=D('8.9900'), plu='123', vat_code='zero')
        self.theirs = ProductFactory(company=CompanyFactory(), name='Fake Their Rice')
        BarcodeRule.objects.create(company=self.company, name='Fake scale', prefix='21', item_code_length=5,
                                   value_type='weight', decimals=3)
        BarcodeRule.objects.create(company=self.company, prefix='22', item_code_length=5, value_type='price',
                                   is_active=False)  # not sent
        long_ago = timezone.now() - timedelta(hours=1)
        Product.objects.update(updated_at=long_ago)  # as if nothing changed lately

    def get(self, client=None, **params):
        response = (client or self.client).get(reverse('till-catalogue'), params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def names(self, body):
        return {p['name'] for p in body['products']}

    def test_full_snapshot(self):
        body = self.get()
        self.assertTrue(body['full'])
        self.assertEqual(self.names(body), {'Fake Rice', 'Fake Mince'})  # never another company's
        mince = next(p for p in body['products'] if p['name'] == 'Fake Mince')
        self.assertEqual({k: mince[k] for k in ('sold_by', 'price_per_kg', 'plu', 'vat_code', 'price')},
                         {'sold_by': 'weight', 'price_per_kg': '8.9900', 'plu': '123', 'vat_code': 'zero',
                          'price': '0.00'})
        self.assertNotIn('external_id', mince)
        self.assertIn('commodity_code', mince)  # the till's search box also matches it
        self.assertEqual((body['user'], body['company']), (self.cashier.pk, self.company.pk))
        self.assertEqual([r['prefix'] for r in body['barcode_rules']], ['21'])
        self.assertEqual([t['id'] for t in body['tills']], [self.till.pk])
        self.assertEqual([b['id'] for b in body['branches']], [self.till.branch_id])
        self.assertIn('code', body['branches'][0])  # tills number offline sales with it (G27)
        self.assertTrue({r['code'] for r in body['vat_rates']} >= {'zero', 'standard'})
        self.assertEqual(body['removed'], [])

    def test_nothing_changed_is_an_empty_delta(self):
        cursor = self.get()['cursor']
        body = self.get(since=cursor)
        self.assertFalse(body['full'])
        self.assertEqual(body['products'], [])
        self.assertGreaterEqual(body['cursor'], cursor)

    def test_price_approval_is_in_the_next_delta(self):
        cursor = self.get()['cursor']
        self.rice.price = D('2.75')
        self.rice.save(update_fields=['price'])  # how a price approval saves it
        body = self.get(since=cursor)
        self.assertEqual([(p['name'], p['price']) for p in body['products']], [('Fake Rice', '2.75')])

    def test_sale_bumps_product_in_delta(self):
        cursor = self.get()['cursor']
        session = self.open()['id']
        response = self.sell(session, [(self.rice, 2)], method='card')
        self.assertEqual(response.status_code, 201, response.content)
        body = self.get(since=cursor)
        self.assertEqual([(p['name'], p['stock_quantity']) for p in body['products']], [('Fake Rice', 8)])

    def test_overlap_resends_changes_just_before_the_cursor(self):
        cursor = self.get()['cursor']
        since = timezone.datetime.fromisoformat(cursor)
        Product.objects.filter(pk=self.rice.pk).update(updated_at=since - timedelta(seconds=30))  # still committing
        Product.objects.filter(pk=self.mince.pk).update(updated_at=since - timedelta(seconds=90))  # already sent
        self.assertEqual(self.names(self.get(since=cursor)), {'Fake Rice'})

    def test_deleted_product_is_removed(self):
        cursor = self.get()['cursor']
        pk = self.mince.pk
        self.mince.delete()
        self.theirs.delete()  # another company's removal never shows
        self.assertEqual(self.get(since=cursor)['removed'], [pk])

    def test_unreadable_since_is_a_full_snapshot(self):
        body = self.get(since='yesterday-ish')
        self.assertTrue(body['full'])
        self.assertEqual(self.names(body), {'Fake Rice', 'Fake Mince'})

    def test_settings_and_their_fallbacks(self):
        self.assertEqual(self.get()['settings'], {'manual_weight_max_kg': '25', 'offline_warn_hours': 4,
                                                  'offline_confirm_hours': 24, 'offline_stop_hours': 72})
        Setting.objects.create(company=self.company, key='manual_weight_max_kg', value='12.5')
        Setting.objects.create(company=self.company, key='offline_warn_hours', value='2')
        Setting.objects.create(company=self.company, key='offline_stop_hours', value='junk')
        settings = self.get()['settings']
        self.assertEqual((settings['manual_weight_max_kg'], settings['offline_warn_hours'],
                          settings['offline_stop_hours']), ('12.5', 2, 72))

    def test_till_operator_reads(self):  # other roles are refused by role_access (tenants/test_operators.py)
        operator = self.as_user(UserFactory(company=self.company, role=TILL))
        self.assertEqual(self.names(self.get(client=operator)), {'Fake Rice', 'Fake Mince'})

    def test_user_without_company_is_403(self):
        response = self.as_user(UserFactory(company=None)).get(reverse('till-catalogue'))
        self.assertEqual(response.status_code, 403)
