from datetime import date
from decimal import Decimal

from django.test import TestCase

from common.tenancy import scope_queryset
from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from inventory.models import Product, StockBatch
from sync.catalogue import barcodes_of, import_catalogue, summary
from sync.models import CatalogueImport, StorefrontExclusion

EXPIRY = date(2027, 1, 31)


def raw(product_id, name, price='1.59', stock=9, **extra):
    return {
        'product_id': product_id, 'name': name, 'price': price, 'stock_quantity': stock, 'net_contents': '',
        'description': 'Tasty', 'category': None, 'is_active': True, **extra,
    }


class BarcodesOfTests(TestCase):
    def test_main_then_alternates_without_blanks_or_repeats(self):
        item = {'barcode': ' 501 ', 'alternate_barcodes': ['', '502', '501', '  ', None]}
        self.assertEqual(barcodes_of(item), ['501', '502'])
        self.assertEqual(barcodes_of({'barcode': None, 'alternate_barcodes': None}), [])
        self.assertEqual(barcodes_of({}), [])


class CatalogueImportTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()

    def run_import(self, products, barcodes=None, **options):
        return import_catalogue(
            self.company, {'products': products, 'categories': {}}, barcodes=barcodes or {}, expiry=EXPIRY, **options
        )

    def outcomes(self, run):
        return {item.m18_product_id: item.outcome for item in run.items.all()}

    def test_running_twice_creates_no_duplicates(self):
        products = [raw('a1', 'Ghee 500g'), raw('a2', 'Rice 5kg', stock=0), raw('a3', 'Dates 1kg')]
        self.run_import(products)
        second = self.run_import(products)
        self.assertEqual(Product.objects.filter(company=self.company).count(), 3)
        self.assertEqual(StockBatch.objects.filter(product__company=self.company).count(), 2)  # only those with stock
        self.assertEqual(set(self.outcomes(second).values()), {'updated'})

    def test_links_an_existing_product_by_barcode(self):
        mine = ProductFactory(company=self.company, name='Our ghee', price=Decimal('4.00'), stock_quantity=3,
                              barcode='5012345678900')
        run = self.run_import([raw('g1', 'Ghee', price='4.49')], barcodes={'g1': ['5000000000001', '5012345678900']})
        item = run.items.get()
        self.assertEqual((item.outcome, item.product, item.m18_price, item.iq_price),
                         ('linked_barcode', mine, Decimal('4.49'), Decimal('4.00')))
        mine.refresh_from_db()
        self.assertEqual((mine.external_id, mine.name, mine.price, mine.stock_quantity),
                         ('g1', 'Our ghee', Decimal('4.00'), 3))
        self.assertEqual(Product.objects.filter(company=self.company).count(), 1)

    def test_links_by_exact_name_ignoring_case_and_spaces(self):
        mine = ProductFactory(company=self.company, name='basmati  rice 5kg')
        ProductFactory(company=self.company, name='Basmati Rice 10kg')
        run = self.run_import([raw('r1', 'Basmati Rice', net_contents='5kg')])
        item = run.items.get()
        self.assertEqual((item.outcome, item.product), ('linked_name', mine))

    def test_two_candidates_is_ambiguous_and_writes_nothing(self):
        one = ProductFactory(company=self.company, barcode='501')
        two = ProductFactory(company=self.company, barcode='501')
        run = self.run_import([raw('x1', 'Something new')], barcodes={'x1': ['501']})
        item = run.items.get()
        self.assertEqual((item.outcome, sorted(item.candidates)), ('ambiguous', sorted([one.pk, two.pk])))
        self.assertEqual(Product.objects.filter(company=self.company).count(), 2)
        self.assertFalse(Product.objects.exclude(external_id='').exists())

    def test_a_product_claimed_earlier_in_the_run_is_not_reused(self):
        mine = ProductFactory(company=self.company, name='Chickpeas')
        run = self.run_import([raw('c1', 'Chickpeas'), raw('c2', 'Chickpeas')])
        # same rule as a later run, where the IQ product is already linked: the second m18 product is its own product
        self.assertEqual(self.outcomes(run), {'c1': 'linked_name', 'c2': 'created'})
        mine.refresh_from_db()
        self.assertEqual(mine.external_id, 'c1')

    def test_blank_barcodes_never_match(self):
        ProductFactory(company=self.company, name='Unrelated', barcode='')
        run = self.run_import([raw('b1', 'Brand new')], barcodes={'b1': barcodes_of({'barcode': None,
                                                                                     'alternate_barcodes': ['', '  ']})})
        self.assertEqual(self.outcomes(run), {'b1': 'created'})

    def test_new_products_take_the_m18_barcode(self):
        self.run_import([raw('n1', 'Tahini')], barcodes={'n1': ['5011', '5012']})
        self.assertEqual(Product.objects.get(external_id='n1').barcode, '5011')

    def test_linking_fills_a_blank_barcode_only(self):
        blank = ProductFactory(company=self.company, name='Tahini', barcode='')
        self.run_import([raw('n1', 'Tahini')], barcodes={'n1': ['5011']})
        blank.refresh_from_db()
        self.assertEqual(blank.barcode, '5011')

    def test_excluded_products_are_skipped(self):
        StorefrontExclusion.objects.create(company=self.company, m18_product_id='e1', name='Gift card')
        run = self.run_import([raw('e1', 'Gift card')])
        self.assertEqual(self.outcomes(run), {'e1': 'excluded'})
        self.assertFalse(Product.objects.filter(company=self.company).exists())

    def test_unpriced_and_inactive_are_skipped_and_need_attention(self):
        run = self.run_import([raw('u1', 'Free sample', price='0'), raw('u2', 'Old line', is_active=False),
                               raw('u3', 'Milk')])
        self.assertEqual(self.outcomes(run), {'u1': 'skipped', 'u2': 'skipped', 'u3': 'created'})
        self.assertEqual(summary(run), 'm18 products: 3 — linked 1, excluded 0, needs attention 2')

    def test_keep_prices_does_not_overwrite_iq_price(self):
        self.run_import([raw('k1', 'Ghee', price='4.00')])
        self.run_import([raw('k1', 'Ghee Gold', price='9.99')], keep_prices=True)
        product = Product.objects.get(external_id='k1')
        self.assertEqual((product.name, product.price), ('Ghee Gold', Decimal('4.00')))
        self.run_import([raw('k1', 'Ghee Gold', price='9.99')])
        product.refresh_from_db()
        self.assertEqual(product.price, Decimal('9.99'))

    def test_an_unpriced_m18_row_never_zeroes_a_linked_price(self):
        mine = ProductFactory(company=self.company, name='Ghee', price=Decimal('4.00'), external_id='z1')
        self.run_import([raw('z1', 'Ghee', price='0')])
        mine.refresh_from_db()
        self.assertEqual(mine.price, Decimal('4.00'))

    def test_a_name_match_with_a_different_barcode_is_not_linked(self):
        other_pack = ProductFactory(company=self.company, name='Ghee', barcode='5099')
        run = self.run_import([raw('g1', 'Ghee')], barcodes={'g1': ['5011']})
        self.assertEqual(self.outcomes(run), {'g1': 'created'})
        other_pack.refresh_from_db()
        self.assertEqual(other_pack.external_id, '')

    def test_dry_run_saves_only_the_report(self):
        ProductFactory(company=self.company, name='Chickpeas')
        run = self.run_import([raw('c1', 'Chickpeas'), raw('d1', 'Dates')], dry_run=True)
        self.assertTrue(run.dry_run)
        self.assertEqual(self.outcomes(run), {'c1': 'linked_name', 'd1': 'created'})
        self.assertEqual(Product.objects.filter(company=self.company).count(), 1)
        self.assertFalse(Product.objects.exclude(external_id='').exists())
        self.assertFalse(StockBatch.objects.exists())

    def test_records_who_ran_it(self):
        user = UserFactory(company=self.company, is_staff=True)
        run = self.run_import([raw('w1', 'Ghee')], run_by=user, source='file')
        self.assertEqual((run.run_by, run.source, run.counts['created']), (user, 'file', 1))

    def test_reports_and_matches_stay_within_the_company(self):
        other = CompanyFactory()
        theirs = ProductFactory(company=other, name='Chickpeas', barcode='501')
        run = self.run_import([raw('c1', 'Chickpeas')], barcodes={'c1': ['501']})
        self.assertEqual(self.outcomes(run), {'c1': 'created'})
        theirs.refresh_from_db()
        self.assertEqual(theirs.external_id, '')
        outsider = UserFactory(company=other)
        self.assertFalse(scope_queryset(CatalogueImport.objects.all(), outsider).exists())
