# sales/tests/test_label_sales.py

"""G18: selling scanned labels — the server re-reads the barcode, takes the kg FEFO, charges the label and flags a
label price far from today's price."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from common.models import Setting
from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.gs1_parser import GS
from inventory.models import StockBatch, StockMovement
from rest_framework.exceptions import ValidationError

from sales.labels import read_label
from sales.models import SaleItem, TillSession
from sales.services import record_sale
from vat.services import uk_date
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
        body = {'session': self.session, 'payment_method': 'card', 'card_auth_code': 'AUTH01', 'items': items}
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

    def test_bad_tolerance_setting_falls_back_to_the_default(self):
        for value in ('NaN', 'Infinity', '-5', 'ten'):
            Setting.objects.update_or_create(company=self.company, key='label_price_tolerance_percent',
                                             defaults={'value': value})
            response = self.charge([self.label(scale_code('20', '00077', '00179'))])
            self.assertEqual(response.status_code, 201, (value, response.content))
            self.assertTrue(response.json()['items'][0]['label_price_flagged'], value)  # 10.5% > the default 10%
            StockBatchFactory(product=self.cake, quantity=1, batch_number=f'C-{value}',
                              expiration_date=timezone.localdate() + timedelta(days=30))

    def test_label_for_a_free_product_is_flagged_unless_free(self):
        self.cake.price = D('0.00')
        self.cake.save()
        line = self.charge([self.label(scale_code('20', '00077', '00150'))]).json()['items'][0]
        self.assertEqual((line['expected_price'], line['label_price_flagged']), ('0.00', True))
        line = self.charge([self.label(scale_code('20', '00077', '00000'))]).json()['items'][0]
        self.assertFalse(line['label_price_flagged'])

    def test_gs1_gtin_only_label_on_an_each_product_charges_its_price(self):
        self.cake.barcode = gtin('0501234567890')[1:]
        self.cake.save()
        line = self.charge([self.label('01' + gtin('0501234567890') + '10LOT1')]).json()['items'][0]
        self.assertEqual((line['line_total'], line['label_price'], line['expected_price'], line['label_price_flagged']),
                         ('2.00', None, None, False))

    def test_catch_weight_weight_label_takes_one_piece_and_its_kg(self):
        lamb = ProductFactory(company=self.company, name='Fake Lamb', price=D('0.00'), stock_quantity=0,
                              sold_by='weight', is_catch_weight=True, price_per_kg=D('12.0000'), plu='43')
        StockBatchFactory(product=lamb, quantity=3, weight_kg=D('1.500'), batch_number='L-1',
                          expiration_date=timezone.localdate() + timedelta(days=5))
        line = self.charge([self.label(scale_code('21', '0043', '00520', with_check=True))]).json()['items'][0]
        self.assertEqual((line['quantity'], line['weight_kg'], line['line_total']), (1, '0.520', '6.24'))
        batch = StockBatch.objects.get(product=lamb)
        self.assertEqual((batch.quantity, batch.weight_kg), (2, D('0.980')))

    def test_product_changed_after_the_label_was_read(self):
        label = read_label(self.company, scale_code('21', '0042', '00512', with_check=True), uk_date())
        StockBatch.objects.filter(product=self.mince).update(weight_kg=0)
        self.mince.__class__.objects.filter(pk=self.mince.pk).update(sold_by='each', price_per_kg=None)
        session = TillSession.objects.create(till=self.till, opened_by=self.cashier, opening_float=D('0.00'))
        with self.assertRaises(ValidationError) as caught:
            record_sale(session=session, cashier=self.cashier, payment_method='card', amount_tendered=None,
                        lines=[(label.product, 1, label.weight_kg, label)])
        self.assertIn('changed, scan it again', str(caught.exception.detail))


class SuperuserLabelSaleTests(SalesTestCase):
    def test_superuser_reads_labels_with_the_sessions_company(self):
        add_rules(self.company)
        cake = self.stocked(price='2.00', batches=((5, 30),), name='Fake Cake', plu='77')
        other = CompanyFactory()
        add_rules(other)
        ProductFactory(company=other, name='Fake Other Cake', price=D('9.00'), plu='78')
        admin = UserFactory(company=None, is_superuser=True, is_staff=True)
        session = self.open()['id']
        api = self.as_user(admin)
        response = api.post(reverse('sale-list'), {'session': session, 'payment_method': 'card', 'card_auth_code': 'AUTH01',
                                                   'items': [{'barcode': scale_code('20', '00078', '00150')}]},
                            format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('PLU 78', str(response.json()['items']))
        response = api.post(reverse('sale-list'), {'session': session, 'payment_method': 'card', 'card_auth_code': 'AUTH01',
                                                   'items': [{'barcode': scale_code('20', '00077', '00150')}]},
                            format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['items'][0]['product'], cake.pk)
