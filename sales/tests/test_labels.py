# sales/tests/test_labels.py

"""G18: reading a scanned scale label or GS1 label into a product, kg and label price (the server never trusts the
till's own reading)."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from common.tests.factories import CompanyFactory, ProductFactory
from inventory.barcode_parser import ean13_check_digit, value_check_digit
from inventory.gs1_parser import GS, gtin_check_digit
from inventory.models import BarcodeRule
from sales.labels import read_label

D = Decimal
TODAY = date(2026, 11, 2)


def scale_code(prefix, item, value, with_check=False):
    body = prefix + item + (str(value_check_digit(value)) if with_check else '') + value
    return body + str(ean13_check_digit(body))


def gtin(first13):
    return first13 + str(gtin_check_digit(first13))


def add_rules(company):
    BarcodeRule.objects.create(company=company, name='Fake price', prefix='20', item_code_length=5,
                               value_type='price', decimals=2, value_check_digit=False)
    BarcodeRule.objects.create(company=company, name='Fake weight', prefix='21', item_code_length=4,
                               value_type='weight', decimals=3, value_check_digit=True)


class ReadLabelTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        add_rules(self.company)
        self.mince = ProductFactory(company=self.company, name='Fake Mince', sold_by='weight', price_per_kg=D('8.9900'),
                                    plu='42')
        self.cake = ProductFactory(company=self.company, name='Fake Cake', price=D('2.00'), plu='77',
                                   barcode='5012345678900')
        self.gtin = gtin('0501234567890')

    def read(self, code):
        return read_label(self.company, code, TODAY)

    def refused(self, code):
        with self.assertRaises(ValidationError) as caught:
            self.read(code)
        return str(caught.exception.detail[0])

    def test_weight_label(self):
        label = self.read(scale_code('21', '0042', '00512', with_check=True))
        self.assertEqual((label.product, label.weight_kg, label.label_price, label.weight_from_price),
                         (self.mince, D('0.512'), None, False))

    def test_plu_matches_without_leading_zeros(self):
        label = self.read(scale_code('20', '00077', '00150'))
        self.assertEqual((label.product, label.label_price, label.weight_kg), (self.cake, D('1.50'), None))

    def test_price_label_on_a_weighed_product_works_out_the_kg(self):
        label = self.read(scale_code('20', '00042', '00460'))
        # 4.60 / 8.99 = 0.51168 -> 0.512 (half-up)
        self.assertEqual((label.weight_kg, label.label_price, label.weight_from_price), (D('0.512'), D('4.60'), True))
        self.assertIn('price is £0.00', self.refused(scale_code('20', '00042', '00000')))

    def test_gs1_weight_and_price(self):
        self.mince.barcode = '5012345678900'
        self.mince.save()
        self.cake.barcode = ''
        self.cake.save()
        label = self.read('01' + self.gtin + '3105051234' + '17261130' + '10LOT1' + GS + '392200460')
        # 0.51234 kg -> 0.512
        self.assertEqual((label.product, label.weight_kg, label.label_price), (self.mince, D('0.512'), D('4.60')))

    def test_gs1_gtin_only_on_an_each_product(self):
        label = self.read(']C1' + '01' + self.gtin + '10LOT1')
        self.assertEqual((label.product, label.weight_kg, label.label_price), (self.cake, None, None))

    def test_gs1_gtin_only_on_a_weighed_product_refused(self):
        self.cake.barcode = ''
        self.cake.save()
        self.mince.barcode = '5012345678900'
        self.mince.save()
        self.assertIn('no weight or price', self.refused('01' + self.gtin + '10LOT1'))

    def test_expired_gs1_label_refused(self):
        self.assertIn('Use-by', self.refused('01' + self.gtin + '17261101'))

    def test_tampered_label_rejected(self):
        good = scale_code('21', '0042', '00512', with_check=True)
        tampered = good[:9] + str((int(good[9]) + 1) % 10) + good[10:]  # a changed weight digit
        self.assertIn('did not read correctly', self.refused(tampered))
        bad_value_check = good[:6] + str((int(good[6]) + 1) % 10) + good[7:12]
        bad_value_check += str(ean13_check_digit(bad_value_check))  # EAN fixed up, verifier still wrong
        self.assertIn('did not read correctly', self.refused(bad_value_check))
        self.assertIn('did not read correctly', self.refused('01' + self.gtin[:-1] + str((int(self.gtin[-1]) + 1) % 10)))

    def test_unknown_prefix_is_an_ordinary_barcode(self):
        self.assertIn('Not a price or weight label', self.refused('5012345678900'))

    def test_no_product_found(self):
        self.assertIn('PLU 99', self.refused(scale_code('20', '00099', '00150')))
        self.assertIn('GTIN', self.refused('01' + gtin('0501234567891')))

    def test_several_products_share_a_gtin(self):
        ProductFactory(company=self.company, name='Fake Cake 2', barcode='05012345678900')
        self.assertIn('Several products', self.refused('01' + self.gtin))

    def test_weight_label_on_an_each_product_refused(self):
        self.assertIn('sold by each', self.refused(scale_code('21', '0077', '00512', with_check=True)))

    def test_weight_over_the_ceiling_refused(self):
        sack = gtin('0501234567892')
        heavy = '01' + sack + '3102012000'  # 120.00 kg
        ProductFactory(company=self.company, name='Fake Sack', sold_by='weight', price_per_kg=D('1.0000'),
                       barcode=sack[1:])
        self.assertIn('0.001 and 100 kg', self.refused(heavy))
        self.assertIn('0.001 and 100 kg', self.refused(scale_code('21', '0042', '00000', with_check=True)))

    def test_other_companys_plu_ignored(self):
        other = CompanyFactory()
        add_rules(other)
        ProductFactory(company=other, name='Fake Other', plu='55')
        self.assertIn('PLU 55', self.refused(scale_code('20', '00055', '00150')))

    def test_inactive_rules_not_used(self):
        BarcodeRule.objects.filter(company=self.company).update(is_active=False)
        self.assertIn('Not a price or weight label', self.refused(scale_code('20', '00077', '00150')))

    def test_price_in_fractions_of_a_penny_refused(self):
        BarcodeRule.objects.create(company=self.company, name='Fake 3 dp price', prefix='22', item_code_length=5,
                                   value_type='price', decimals=3, value_check_digit=False)
        self.assertIn('fractions of a penny', self.refused(scale_code('22', '00077', '01234')))
        self.assertEqual(self.read(scale_code('22', '00077', '01230')).label_price, D('1.23'))
