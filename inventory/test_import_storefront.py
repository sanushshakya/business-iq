import json
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from common.tests.factories import CompanyFactory
from inventory.management.commands.import_storefront_products import fetch_catalog, product_name
from inventory.models import Product, StockBatch

CATEGORIES = {'14': 'Asian', '23': 'European'}


def raw(product_id, name, price='1.59', stock=9, **extra):
    return {
        'product_id': product_id, 'name': name, 'price': price, 'stock_quantity': stock, 'net_contents': '200g',
        'description': 'Tasty', 'category': 14, 'is_active': True, **extra,
    }


class ImportStorefrontProductsTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory(registration_number='M18-TEST')

    def run_import(self, tmp_path, products, **options):
        with open(tmp_path, 'w') as fh:
            json.dump({'products': products, 'categories': CATEGORIES}, fh)
        out = StringIO()
        call_command('import_storefront_products', company='M18-TEST', file=tmp_path, stdout=out, **options)
        return out.getvalue()

    @property
    def tmp(self):
        import tempfile

        return tempfile.mktemp(suffix='.json')

    def test_creates_products_with_category_and_an_opening_batch(self):
        out = self.run_import(self.tmp, [raw('a', 'Jaimin Murukku'), raw('b', 'Ghee 500g', price='6.75', stock=0, category=23)])
        self.assertIn('2 new, 0 updated, 0 skipped', out)
        murukku = Product.objects.get(external_id='a')
        self.assertEqual((murukku.name, str(murukku.price), murukku.stock_quantity, murukku.category.name),
                         ('Jaimin Murukku 200g', '1.59', 9, 'Asian'))
        batch = StockBatch.objects.get(product=murukku)
        self.assertEqual((batch.batch_number, batch.quantity), ('OPENING', 9))
        ghee = Product.objects.get(external_id='b')
        self.assertEqual(ghee.name, 'Ghee 500g')  # already has a size
        self.assertFalse(StockBatch.objects.filter(product=ghee).exists())  # nothing in stock, nothing to batch

    def test_rerun_updates_price_but_never_touches_stock(self):
        path = self.tmp
        self.run_import(path, [raw('a', 'Rice', price='1.00', stock=9)])
        Product.objects.filter(external_id='a').update(stock_quantity=3)
        out = self.run_import(path, [raw('a', 'Rice', price='1.25', stock=50, description='New')])
        self.assertIn('0 new, 1 updated', out)
        product = Product.objects.get(external_id='a')
        self.assertEqual((str(product.price), product.description, product.stock_quantity), ('1.25', 'New', 3))
        self.assertEqual(StockBatch.objects.filter(product=product).count(), 1)

    def test_same_name_different_product_stay_separate(self):
        self.run_import(self.tmp, [raw('a', 'Oba Macaroni 400g', price='0.89'), raw('b', 'Oba Macaroni 400g', price='1.19')])
        self.assertEqual(Product.objects.filter(company=self.company, name='Oba Macaroni 400g').count(), 2)

    def test_skips_unpriced_and_inactive(self):
        out = self.run_import(self.tmp, [raw('a', 'Free', price='0.00'), raw('b', 'Gone', is_active=False), raw('c', 'Fine')])
        self.assertIn('1 new, 0 updated, 2 skipped', out)
        self.assertIn('skipped Free', out)
        self.assertEqual(list(Product.objects.values_list('external_id', flat=True)), ['c'])

    def test_dry_run_writes_nothing(self):
        out = self.run_import(self.tmp, [raw('a', 'Rice')], dry_run=True)
        self.assertIn('Would import: 1 new', out)
        self.assertEqual(Product.objects.count(), 0)

    def test_expiry_is_configurable(self):
        self.run_import(self.tmp, [raw('a', 'Rice')], expiry_days=10)
        batch = StockBatch.objects.get()
        from django.utils import timezone

        self.assertEqual((batch.expiration_date - timezone.localdate()).days, 10)

    def test_unknown_company(self):
        with self.assertRaises(CommandError):
            call_command('import_storefront_products', company='NOPE', file=self.tmp, stdout=StringIO())

    def test_product_name(self):
        self.assertEqual(product_name({'name': ' Ghee  ', 'net_contents': '500g'}), 'Ghee 500g')
        self.assertEqual(product_name({'name': 'Ghee 500g', 'net_contents': '500g'}), 'Ghee 500g')
        self.assertEqual(product_name({'name': 'Ghee', 'net_contents': ''}), 'Ghee')


class FetchCatalogTests(TestCase):
    def test_walks_the_pages_and_maps_category_names(self):
        responses = {
            'https://shop.test/api/products/?limit=100&offset=0': {'results': [{'product_id': 'a'}], 'next': 'https://shop.test/api/products/?limit=100&offset=100'},
            'https://shop.test/api/products/?limit=100&offset=100': {'results': [{'product_id': 'b'}], 'next': None},
            'https://shop.test/api/categories/': [{'id': 14, 'name': 'Asian'}],
        }

        def fake_get(url, **kwargs):
            return mock.Mock(json=lambda: responses[url], raise_for_status=lambda: None)

        with mock.patch('inventory.management.commands.import_storefront_products.requests.get', fake_get):
            with mock.patch('inventory.management.commands.import_storefront_products.time.sleep'):
                catalog = fetch_catalog('https://shop.test/api')
        self.assertEqual([p['product_id'] for p in catalog['products']], ['a', 'b'])
        self.assertEqual(catalog['categories'], {'14': 'Asian'})
