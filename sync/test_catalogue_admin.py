from datetime import date

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from sync.catalogue import import_catalogue
from sync.models import CatalogueImportItem, StorefrontExclusion


def raw(product_id, name, price='1.59'):
    return {'product_id': product_id, 'name': name, 'price': price, 'stock_quantity': 0, 'net_contents': '',
            'description': '', 'category': None, 'is_active': True}


class CatalogueAdminTests(TestCase):
    """Staff review an import in the admin: link an item by hand, or mark it not sold online."""

    def setUp(self):
        self.company, self.other = CompanyFactory(), CompanyFactory()
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.staff.user_permissions.set(Permission.objects.filter(content_type__app_label__in=['sync', 'inventory']))
        self.web = self.client_class()
        self.web.force_login(self.staff)

    def run_import(self, products, company=None):
        return import_catalogue(company or self.company, {'products': products, 'categories': {}}, barcodes={},
                                expiry=date(2027, 1, 31))

    def item(self, run, m18_id):
        return run.items.get(m18_product_id=m18_id)

    def change_url(self, item):
        return reverse('admin:sync_catalogueimportitem_change', args=[item.pk])

    def test_mark_not_sold_online_creates_exclusions(self):
        run = self.run_import([raw('s1', 'Sample', price='0'), raw('s2', 'Gift card', price='0')])
        ids = [item.pk for item in run.items.all()]
        for _ in range(2):  # a second time changes nothing
            response = self.web.post(reverse('admin:sync_catalogueimportitem_changelist'),
                                     {'action': 'mark_not_sold_online', '_selected_action': ids})
            self.assertEqual(response.status_code, 302)
        exclusions = StorefrontExclusion.objects.filter(company=self.company)
        self.assertEqual(sorted(e.m18_product_id for e in exclusions), ['s1', 's2'])
        self.assertEqual({e.excluded_by for e in exclusions}, {self.staff})
        self.assertEqual(self.run_import([raw('s1', 'Sample', price='0')]).items.get().outcome, 'excluded')

    def test_link_manually_sets_the_external_id(self):
        one, two = ProductFactory(company=self.company, name='Tea'), ProductFactory(company=self.company, name='Tea')
        item = self.item(self.run_import([raw('t1', 'Tea')]), 't1')
        self.assertEqual(item.outcome, 'ambiguous')
        response = self.web.post(self.change_url(item), {'link_to': two.pk})
        self.assertEqual(response.status_code, 302, response.content[:2000])
        two.refresh_from_db()
        one.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual((two.external_id, one.external_id), ('t1', ''))
        self.assertEqual((item.product, item.resolved_by), (two, self.staff))
        self.assertIsNotNone(item.resolved_at)
        self.assertEqual(self.item(self.run_import([raw('t1', 'Tea')]), 't1').outcome, 'updated')

    def test_link_manually_refuses_an_already_linked_m18_id(self):
        ProductFactory(company=self.company, name='Tea')
        ProductFactory(company=self.company, name='Tea')
        item = self.item(self.run_import([raw('t1', 'Tea')]), 't1')
        ProductFactory(company=self.company, name='Linked elsewhere', external_id='t1')
        target = ProductFactory(company=self.company, name='Spare')
        response = self.web.post(self.change_url(item), {'link_to': target.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already linked')
        target.refresh_from_db()
        self.assertEqual(target.external_id, '')

    def test_only_unresolved_ambiguous_or_skipped_items_can_be_linked(self):
        item = self.item(self.run_import([raw('n1', 'Brand new')]), 'n1')
        self.assertEqual(item.outcome, 'created')
        target = ProductFactory(company=self.company, name='Spare')
        self.web.post(self.change_url(item), {'link_to': target.pk})
        target.refresh_from_db()
        self.assertEqual(target.external_id, '')

    def test_other_companies_items_and_products_are_hidden(self):
        ProductFactory(company=self.company, name='Tea')
        ProductFactory(company=self.company, name='Tea')
        theirs = self.item(self.run_import([raw('x1', 'Rice', price='0')], company=self.other), 'x1')
        self.assertNotEqual(self.web.get(self.change_url(theirs), follow=True).status_code, 500)
        self.assertFalse(CatalogueImportItem.objects.filter(pk=theirs.pk, run__company=self.company).exists())
        self.assertNotContains(self.web.get(reverse('admin:sync_catalogueimportitem_changelist')), 'Rice')
        outsider = ProductFactory(company=self.other, name='Their tea')
        mine = self.item(self.run_import([raw('t1', 'Tea')]), 't1')
        page = self.web.get(self.change_url(mine))
        self.assertContains(page, 'name="link_to"')
        self.assertNotContains(page, 'Their tea')
        self.web.post(self.change_url(mine), {'link_to': outsider.pk})
        outsider.refresh_from_db()
        self.assertEqual(outsider.external_id, '')

    def test_the_import_report_is_read_only(self):
        run = self.run_import([raw('a1', 'Ghee')])
        self.assertEqual(self.web.get(reverse('admin:sync_catalogueimport_change', args=[run.pk])).status_code, 200)
        self.assertEqual(self.web.get(reverse('admin:sync_catalogueimport_add')).status_code, 403)
        self.assertEqual(self.web.get(reverse('admin:sync_catalogueimportitem_add')).status_code, 403)
