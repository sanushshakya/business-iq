# inventory/test_gs1_lookup.py

"""G16: finding the company's products for a GS1 GTIN, in any of the forms a product barcode is stored in."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from common.tests.factories import CompanyFactory, ProductFactory
from inventory.barcodes import products_for_gtin, read_gs1
from inventory.gs1_parser import GS

GTIN = '05012345678900'


class GtinLookupTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()

    def product(self, barcode, company=None, **fields):
        return ProductFactory(company=company or self.company, barcode=barcode, **fields)

    def found(self, gtin):
        return sorted(p.pk for p in products_for_gtin(self.company, gtin))

    def test_gtin_13_and_14_forms(self):
        ean13, gtin14 = self.product('5012345678900'), self.product(GTIN)
        self.assertEqual(self.found(GTIN), sorted([ean13.pk, gtin14.pk]))

    def test_stored_barcode_padded(self):
        padded = self.product(' 5012345678900 ')
        self.assertEqual(self.found(GTIN), [padded.pk])

    def test_upc_12(self):
        upc = self.product('012345678905')
        self.assertEqual(self.found('00012345678905'), [upc.pk])

    def test_ean_8(self):
        ean8 = self.product('96385074')
        self.assertEqual(self.found('00000096385074'), [ean8.pk])

    def test_other_forms_only_when_the_leading_zeros_are_there(self):
        self.product('5012345678900')
        self.product('12345678900')  # not a form of this GTIN
        self.assertEqual(self.found('15012345678907'), [])

    def test_other_companys_product_never_returned(self):
        self.product('5012345678900', company=CompanyFactory())
        self.assertEqual(self.found(GTIN), [])

    def test_no_gtin_and_blank_barcodes(self):
        self.product('')
        self.assertEqual(self.found(None), [])
        self.assertEqual(self.found(''), [])

    def test_read_gs1_parses_and_finds(self):
        mince = self.product('5012345678900', name='Fake Mince')
        parsed, products = read_gs1(self.company, '01' + GTIN + '3103000512' + '17261031' + '10LOT1' + GS,
                                    today=date(2026, 11, 1))
        self.assertEqual([p.pk for p in products], [mince.pk])
        self.assertEqual((parsed.weight_kg, parsed.expired, parsed.batch), (Decimal('0.512'), True, 'LOT1'))

    def test_read_gs1_without_a_gtin(self):
        self.product('5012345678900')
        parsed, products = read_gs1(self.company, '10LOT1', today=date(2026, 11, 1))
        self.assertEqual((parsed.batch, products), ('LOT1', []))

    def test_read_gs1_defaults_to_the_uk_date(self):
        from unittest import mock

        with mock.patch('inventory.barcodes.uk_date', return_value=date(2026, 11, 2)):
            parsed, _ = read_gs1(self.company, '17261101')
        self.assertTrue(parsed.expired)
