# sales/tests/test_vat_on_sales.py

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from sales.models import SaleItem
from sales.tests.test_sales import SalesTestCase
from vat.models import VatRate


class ProductVatCodeTests(SalesTestCase):
    def test_product_vat_code_defaults_to_zero_and_is_writable(self):
        product = self.stocked()
        self.assertEqual(product.vat_code, 'zero')
        url = reverse('product-detail', args=[product.pk])
        response = self.as_user(self.manager).patch(url, {'vat_code': 'standard'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['vat_code'], 'standard')
        bad = self.as_user(self.manager).patch(url, {'vat_code': 'luxury'}, format='json')
        self.assertEqual(bad.status_code, 400)


class SaleLineVatTests(SalesTestCase):
    def basket(self):
        bread = self.stocked(price='1.10', name='Fake Bread')
        cola = self.stocked(price='1.25', name='Fake Cola', vat_code='standard')
        return bread, cola

    def test_mixed_zero_standard_basket_to_the_penny(self):
        bread, cola = self.basket()
        session = self.open()
        response = self.sell(session['id'], [(bread, 2), (cola, 3)], method='card')
        self.assertEqual(response.status_code, 201, response.content)
        lines = {i.product_name: i for i in SaleItem.objects.filter(sale_id=response.json()['id'])}
        bread_line, cola_line = lines['Fake Bread'], lines['Fake Cola']
        self.assertEqual(
            (bread_line.net_amount, bread_line.vat_amount, bread_line.line_total),
            (Decimal('2.20'), Decimal('0.00'), Decimal('2.20')),
        )
        # 3.75 x 20/120 = 0.625 -> 0.63
        self.assertEqual(
            (cola_line.net_amount, cola_line.vat_amount, cola_line.line_total),
            (Decimal('3.12'), Decimal('0.63'), Decimal('3.75')),
        )
        self.assertEqual(response.json()['total'], '5.95')

    def test_line_stores_rate_used(self):
        _bread, cola = self.basket()
        response = self.sell(self.open()['id'], [(cola, 1)], method='card')
        line = SaleItem.objects.get(sale_id=response.json()['id'])
        self.assertEqual((line.vat_code, line.vat_rate), ('standard', Decimal('20.00')))

    def test_editing_a_rate_does_not_change_past_sales(self):
        _bread, cola = self.basket()
        session = self.open()
        first = self.sell(session['id'], [(cola, 3)], method='card').json()
        rate = VatRate.objects.get(code='standard', valid_from='2011-01-04')
        rate.rate = Decimal('17.50')  # an edit to the row the first sale used
        rate.save()
        VatRate.objects.create(code='standard', rate=Decimal('15.00'), valid_from=timezone.localdate())
        second = self.sell(session['id'], [(cola, 3)], method='card').json()
        old = SaleItem.objects.get(sale_id=first['id'])
        new = SaleItem.objects.get(sale_id=second['id'])
        self.assertEqual((old.vat_rate, old.vat_amount, old.net_amount), (Decimal('20.00'), Decimal('0.63'), Decimal('3.12')))
        # 3.75 x 15/115 = 0.4891 -> 0.49
        self.assertEqual((new.vat_rate, new.vat_amount), (Decimal('15.00'), Decimal('0.49')))
        refetched = self.client.get(reverse('sale-detail', args=[first['id']])).json()
        self.assertEqual(refetched['items'][0]['vat_amount'], '0.63')

    def test_missing_rate_refuses_sale(self):
        _bread, cola = self.basket()
        VatRate.objects.filter(code='standard').delete()
        response = self.sell(self.open()['id'], [(cola, 1)], method='card')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Fake Cola', str(response.json()))
        self.assertFalse(SaleItem.objects.exists())

    def test_sale_api_returns_vat_fields_as_strings(self):
        _bread, cola = self.basket()
        item = self.sell(self.open()['id'], [(cola, 1)], method='card').json()['items'][0]
        self.assertEqual(
            {k: item[k] for k in ('vat_code', 'vat_rate', 'net_amount', 'vat_amount', 'line_total')},
            {'vat_code': 'standard', 'vat_rate': '20.00', 'net_amount': '1.04', 'vat_amount': '0.21',
             'line_total': '1.25'},
        )
