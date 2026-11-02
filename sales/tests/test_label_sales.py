# sales/tests/test_label_sales.py

"""G18: selling scanned labels — the server re-reads the barcode, takes the kg FEFO, charges the label and flags a
label price far from today's price."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from common.models import Setting
from common.tests.factories import ProductFactory, StockBatchFactory
from inventory.gs1_parser import GS
from inventory.models import StockBatch, StockMovement
from sales.models import SaleItem
from sales.tests.test_labels import add_rules, gtin, scale_code
from sales.tests.test_sales import SalesTestCase

D = Decimal


class LabelSaleTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        add_rules(self.company)
        self.mince = ProductFactory(company=self.company, name='Fake Mince', price=D('0.00'), stock_quantity=0,
                                    sold_by='weight', price_per_kg=D('8.9900'), plu='42')
        StockBatchFactory(product=self.mince, quantity=0, weight_kg=D('2.000'), batch_number='M-1',
                          expiration_date=timezone.localdate() + timedelta(days=5))
        self.cake = self.stocked(price='2.00', batches=((5, 30),), name='Fake Cake', plu='77')

    def charge(self, items, client_ref=None):
        body = {'session': self.session, 'payment_method': 'card', 'items': items}
        if client_ref:
            body['client_ref'] = client_ref
        return self.client.post(reverse('sale-list'), body, format='json')

    def label(self, code):
        return {'barcode': code}

    @property
    def session(self):
        if not hasattr(self, '_session'):
            self._session = self.open()['id']
        return self._session

    def kg_left(self):
        return StockBatch.objects.get(product=self.mince).weight_kg

    def test_weight_label_sold_by_kg(self):
        code = scale_code('21', '0042', '00512', with_check=True)
        response = self.charge([self.label(code)])
        self.assertEqual(response.status_code, 201, response.content)
        line = response.json()['items'][0]
        # 0.512 × 8.99 = 4.60288 -> 4.60
        self.assertEqual((line['weight_kg'], line['price_per_kg'], line['line_total'], line['barcode']),
                         ('0.512', '8.9900', '4.60', code))
        self.assertEqual((line['label_price'], line['expected_price'], line['label_price_flagged']), (None, None, False))
        self.assertEqual(self.kg_left(), D('1.488'))
        self.assertEqual(StockMovement.objects.get(movement_type='outbound').weight_kg, D('0.512'))

    def test_far_label_price_sold_and_flagged(self):
        cases = (('00100', '1.00', True), ('00185', '1.85', False), ('00179', '1.79', True), ('00221', '2.21', True))
        for value, price, flagged in cases:
            response = self.charge([self.label(scale_code('20', '00077', value))])
            self.assertEqual(response.status_code, 201, response.content)
            line = response.json()['items'][0]
            self.assertEqual((line['line_total'], line['label_price'], line['expected_price'],
                              line['label_price_flagged']), (price, price, '2.00', flagged), value)
        Setting.objects.create(company=self.company, key='label_price_tolerance_percent', value='50')
        line = self.charge([self.label(scale_code('20', '00077', '00100'))]).json()['items'][0]
        self.assertFalse(line['label_price_flagged'])

    def test_gs1_weight_and_price_label(self):
        self.mince.barcode = gtin('0501234567890')
        self.mince.save()
        expiry = (timezone.localdate() + timedelta(days=3)).strftime('%y%m%d')
        honest = '01' + self.mince.barcode + '3103000512' + '17' + expiry + '10LOT1' + GS + '392200460'
        line = self.charge([self.label(honest)]).json()['items'][0]
        self.assertEqual((line['line_total'], line['expected_price'], line['label_price_flagged']),
                         ('4.60', '4.60', False))
        cheap = '01' + self.mince.barcode + '3103000512' + '392200100'
        line = self.charge([self.label(cheap)]).json()['items'][0]
        self.assertEqual((line['line_total'], line['weight_kg'], line['label_price_flagged']), ('1.00', '0.512', True))
        self.assertEqual(self.kg_left(), D('0.976'))

    def test_price_only_weighed_label(self):
        line = self.charge([self.label(scale_code('20', '00042', '00460'))]).json()['items'][0]
        self.assertEqual((line['line_total'], line['weight_kg'], line['label_price'], line['expected_price'],
                          line['label_price_flagged']), ('4.60', '0.512', '4.60', None, False))
        self.assertEqual(self.kg_left(), D('1.488'))

    def test_tampered_label_rejected_at_charge(self):
        good = scale_code('21', '0042', '00512', with_check=True)
        tampered = good[:9] + str((int(good[9]) + 1) % 10) + good[10:]
        response = self.charge([{'product': self.cake.pk, 'quantity': 1}, self.label(tampered)])
        self.assertEqual(response.status_code, 400)
        self.assertIn('did not read correctly', str(response.json()['items']))
        self.assertFalse(SaleItem.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_a_label_line_is_only_the_barcode(self):
        code = scale_code('20', '00077', '00150')
        for extra in ({'product': self.cake.pk}, {'quantity': 2}, {'weight_kg': '0.500'}):
            response = self.charge([{**self.label(code), **extra}])
            self.assertEqual(response.status_code, 400, extra)
        self.assertEqual(self.charge([{}]).status_code, 400)

    def test_same_label_twice_is_two_lines(self):
        code = scale_code('21', '0042', '00512', with_check=True)
        response = self.charge([self.label(code), self.label(code)])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(len(response.json()['items']), 2)
        self.assertEqual(response.json()['total'], '9.20')
        self.assertEqual(self.kg_left(), D('0.976'))

    def test_label_and_plain_lines_share_stock(self):
        label = self.label(scale_code('20', '00077', '00150'))
        response = self.charge([{'product': self.cake.pk, 'quantity': 5}, label])
        self.assertEqual(response.status_code, 400)
        self.assertIn('only 5 in date, 6 requested', str(response.json()['items']))
        response = self.charge([{'product': self.cake.pk, 'quantity': 4}, label])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual([(i['quantity'], i['line_total']) for i in response.json()['items']],
                         [(4, '8.00'), (1, '1.50')])
        self.assertEqual(StockBatch.objects.get(product=self.cake).quantity, 0)

    def test_label_sale_retry_not_double(self):
        code, ref = scale_code('21', '0042', '00512', with_check=True), str(uuid.uuid4())
        first, again = self.charge([self.label(code)], client_ref=ref), self.charge([self.label(code)], client_ref=ref)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(self.kg_left(), D('1.488'))

    def test_void_puts_the_label_kg_back(self):
        sale = self.charge([self.label(scale_code('21', '0042', '00512', with_check=True))]).json()
        response = self.client.post(reverse('sale-void', args=[sale['id']]), {'reason': 'Fake mistake'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.kg_left(), D('2.000'))

    def test_vat_split_from_the_label_total(self):
        self.cake.vat_code = 'standard'
        self.cake.save()
        line = self.charge([self.label(scale_code('20', '00077', '00150'))]).json()['items'][0]
        # 1.50 gross at 20%: VAT 0.25, net 1.25
        self.assertEqual((line['vat_amount'], line['net_amount']), ('0.25', '1.25'))
