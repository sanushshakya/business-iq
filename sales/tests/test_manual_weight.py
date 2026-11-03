# sales/tests/test_manual_weight.py

"""G19: a weight the cashier types (no label, no scale agent yet) is limited per line and marked for audit."""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from common.models import Setting
from common.tests.factories import ProductFactory, StockBatchFactory
from inventory.models import StockBatch

from sales.models import SaleItem
from sales.tests.test_labels import add_rules, scale_code
from sales.tests.test_sales import SalesTestCase

D = Decimal


class ManualWeightTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.mince = ProductFactory(company=self.company, name='Fake Mince', price=D('0.00'), stock_quantity=0,
                                    sold_by='weight', price_per_kg=D('8.9900'), plu='42')
        StockBatchFactory(product=self.mince, quantity=0, weight_kg=D('60.000'), batch_number='M-1',
                          expiration_date=timezone.localdate() + timedelta(days=5))
        self.session = self.open()['id']

    def charge(self, items):
        body = {'session': self.session, 'payment_method': 'card', 'items': items}
        return self.client.post(reverse('sale-list'), body, format='json')

    def test_typed_weight_is_marked_manual(self):
        response = self.charge([{'product': self.mince.pk, 'weight_kg': '0.512'}])
        self.assertEqual(response.status_code, 201, response.content)
        item = response.data['items'][0]
        self.assertEqual(item['weight_kg'], '0.512')
        self.assertEqual(item['line_total'], '4.60')
        self.assertIs(item['manual_weight'], True)
        self.assertEqual(StockBatch.objects.get(product=self.mince).weight_kg, D('59.488'))

    def test_label_weight_is_not_manual(self):
        add_rules(self.company)
        response = self.charge([{'barcode': scale_code('21', '0042', '00512', with_check=True)}])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIs(response.data['items'][0]['manual_weight'], False)

    def test_each_line_is_not_manual(self):
        cake = self.stocked(price='2.00', batches=((5, 30),), name='Fake Cake')
        response = self.charge([{'product': cake.pk, 'quantity': 1}])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIs(response.data['items'][0]['manual_weight'], False)

    def test_manual_weight_over_the_default_limit_refused(self):
        response = self.charge([{'product': self.mince.pk, 'weight_kg': '25.001'}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['items'], [
            'Fake Mince: 25.001 kg is over the 25 kg limit for a typed weight. Check it, or weigh it in parts.'])
        self.assertFalse(SaleItem.objects.exists())
        self.assertEqual(StockBatch.objects.get(product=self.mince).weight_kg, D('60.000'))

    def test_manual_weight_at_the_limit_sold(self):
        response = self.charge([{'product': self.mince.pk, 'weight_kg': '25.000'}])
        self.assertEqual(response.status_code, 201, response.content)

    def test_limit_is_per_line(self):
        response = self.charge([{'product': self.mince.pk, 'weight_kg': '20.000'},
                                {'product': self.mince.pk, 'weight_kg': '20.000'}])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(SaleItem.objects.filter(manual_weight=True).count(), 2)

    def test_limit_comes_from_the_company_setting(self):
        Setting.objects.create(company=self.company, key='manual_weight_max_kg', value='2')
        self.assertEqual(self.charge([{'product': self.mince.pk, 'weight_kg': '2.001'}]).status_code, 400)
        self.assertEqual(self.charge([{'product': self.mince.pk, 'weight_kg': '2.000'}]).status_code, 201)

    def test_bad_setting_falls_back_to_the_default(self):
        for value in ('NaN', '-1', '0', 'lots'):
            with self.subTest(value=value):
                Setting.objects.update_or_create(company=self.company, key='manual_weight_max_kg',
                                                 defaults={'value': value})
                self.assertEqual(self.charge([{'product': self.mince.pk, 'weight_kg': '1.000'}]).status_code, 201)
                self.assertEqual(self.charge([{'product': self.mince.pk, 'weight_kg': '25.001'}]).status_code,
                                 400)

    def test_label_lines_are_not_held_to_the_typed_limit(self):
        add_rules(self.company)
        Setting.objects.create(company=self.company, key='manual_weight_max_kg', value='0.5')
        response = self.charge([{'barcode': scale_code('21', '0042', '00512', with_check=True)}])
        self.assertEqual(response.status_code, 201, response.content)

    def test_catch_weight_limit_is_on_the_line_weight(self):
        packs = ProductFactory(company=self.company, name='Fake Lamb Legs', price=D('0.00'), stock_quantity=0,
                               sold_by='weight', is_catch_weight=True, price_per_kg=D('12.0000'))
        StockBatchFactory(product=packs, quantity=20, weight_kg=D('60.000'), batch_number='L-1',
                          expiration_date=timezone.localdate() + timedelta(days=5))
        Setting.objects.create(company=self.company, key='manual_weight_max_kg', value='5')
        over = self.charge([{'product': packs.pk, 'quantity': 3, 'weight_kg': '5.100'}])
        self.assertEqual(over.status_code, 400)
        sold = self.charge([{'product': packs.pk, 'quantity': 2, 'weight_kg': '4.800'}])
        self.assertEqual(sold.status_code, 201, sold.content)
        self.assertIs(sold.data['items'][0]['manual_weight'], True)

    def test_limit_message_prints_the_setting_plainly(self):
        Setting.objects.create(company=self.company, key='manual_weight_max_kg', value='2.5000')
        response = self.charge([{'product': self.mince.pk, 'weight_kg': '3.000'}])
        self.assertIn('over the 2.5 kg limit', response.data['items'][0])
        Setting.objects.filter(company=self.company).update(value='1E+1')
        response = self.charge([{'product': self.mince.pk, 'weight_kg': '11.000'}])
        self.assertIn('over the 10 kg limit', response.data['items'][0])

    def test_label_and_typed_line_of_one_product(self):
        add_rules(self.company)
        Setting.objects.create(company=self.company, key='manual_weight_max_kg', value='0.5')
        label = {'barcode': scale_code('21', '0042', '00512', with_check=True)}
        over = self.charge([label, {'product': self.mince.pk, 'weight_kg': '0.600'}])
        self.assertEqual(over.status_code, 400)
        self.assertEqual(len(over.data['items']), 1)
        self.assertIn('0.600 kg is over the 0.5 kg limit', over.data['items'][0])
        sold = self.charge([label, {'product': self.mince.pk, 'weight_kg': '0.400'}])
        self.assertEqual(sold.status_code, 201, sold.content)
        self.assertEqual([item['manual_weight'] for item in sold.data['items']], [False, True])
