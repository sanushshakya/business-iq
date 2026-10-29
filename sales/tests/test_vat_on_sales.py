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


class VatSummaryTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.bread = self.stocked(price='1.10', name='Fake Bread')
        self.cola = self.stocked(price='1.25', name='Fake Cola', vat_code='standard')
        self.juice = self.stocked(price='2.10', name='Fake Juice', vat_code='reduced')

    def test_sale_vat_summary_groups_by_rate(self):
        sale = self.sell(self.open()['id'], [(self.bread, 2), (self.cola, 3), (self.juice, 1)], method='card').json()
        self.assertTrue(sale['vat_recorded'])
        self.assertEqual(sale['vat_summary'], [
            {'vat_code': 'zero', 'rate': '0.00', 'net': '2.20', 'vat': '0.00', 'gross': '2.20'},
            {'vat_code': 'reduced', 'rate': '5.00', 'net': '2.00', 'vat': '0.10', 'gross': '2.10'},
            {'vat_code': 'standard', 'rate': '20.00', 'net': '3.12', 'vat': '0.63', 'gross': '3.75'},
        ])

    def test_summary_is_the_sum_of_rounded_lines(self):
        # Two separate products at 20%: 0.21 -> VAT 0.04 each (half-penny edge), so the group is 0.08, not 0.07.
        a = self.stocked(price='0.21', name='Fake Sweet A', vat_code='standard')
        b = self.stocked(price='0.21', name='Fake Sweet B', vat_code='standard')
        sale = self.sell(self.open()['id'], [(a, 1), (b, 1)], method='card').json()
        self.assertEqual(sale['vat_summary'], [
            {'vat_code': 'standard', 'rate': '20.00', 'net': '0.34', 'vat': '0.08', 'gross': '0.42'},
        ])

    def test_old_sale_without_vat_is_not_recorded(self):
        sale = self.sell(self.open()['id'], [(self.bread, 1), (self.cola, 1)], method='card').json()
        SaleItem.objects.filter(sale_id=sale['id'], product=self.cola).update(
            vat_code='', vat_rate=None, net_amount=None, vat_amount=None
        )  # as a line sold before VAT was recorded
        body = self.client.get(reverse('sale-detail', args=[sale['id']])).json()
        self.assertFalse(body['vat_recorded'])
        self.assertEqual([row['vat_code'] for row in body['vat_summary']], ['zero'])

    def session_summary(self, session_id):
        return self.client.get(reverse('tillsession-detail', args=[session_id])).json()['summary']

    def test_session_vat_by_rate_excludes_voided(self):
        session = self.open()
        self.sell(session['id'], [(self.cola, 3)], method='card')
        self.sell(session['id'], [(self.bread, 2), (self.cola, 1)], method='card')
        voided = self.sell(session['id'], [(self.cola, 2)], method='card').json()
        self.assertEqual(
            self.as_user(self.manager).post(reverse('sale-void', args=[voided['id']]), {}).status_code, 200
        )
        summary = self.session_summary(session['id'])
        self.assertEqual(summary['vat_by_rate'], [
            {'vat_code': 'zero', 'rate': '0.00', 'net': '2.20', 'vat': '0.00', 'gross': '2.20'},
            # 3.12 + 1.04 net, 0.63 + 0.21 VAT
            {'vat_code': 'standard', 'rate': '20.00', 'net': '4.16', 'vat': '0.84', 'gross': '5.00'},
        ])
        self.assertEqual(summary['vat_not_recorded_total'], '0.00')

    def test_session_reports_gross_without_vat(self):
        session = self.open()
        sale = self.sell(session['id'], [(self.cola, 2)], method='card').json()
        SaleItem.objects.filter(sale_id=sale['id']).update(vat_code='', vat_rate=None, net_amount=None, vat_amount=None)
        summary = self.session_summary(session['id'])
        self.assertEqual(summary['vat_by_rate'], [])
        self.assertEqual(summary['vat_not_recorded_total'], '2.50')

    def test_schema_documents_vat_fields(self):
        from drf_spectacular.generators import SchemaGenerator

        schemas = SchemaGenerator().get_schema(request=None, public=True)['components']['schemas']
        self.assertIn('vat_summary', schemas['Sale']['properties'])
        self.assertIn('vat_by_rate', schemas['SessionSummary']['properties'])


class VatDateTests(SalesTestCase):
    def test_rate_follows_the_uk_date_not_utc(self):
        """23:30 UTC on 30 June 2030 is 00:30 on 1 July in London (BST): a rate starting 1 July applies."""
        from datetime import date, datetime
        from datetime import timezone as dt_timezone
        from unittest import mock

        cola = self.stocked(price='1.25', name='Fake Cola', vat_code='standard', batches=((10, 3650),))
        VatRate.objects.create(code='standard', rate=Decimal('15.00'), valid_from=date(2030, 7, 1))
        session = self.open()
        moment = datetime(2030, 6, 30, 23, 30, tzinfo=dt_timezone.utc)
        with mock.patch('django.utils.timezone.now', return_value=moment):
            response = self.sell(session['id'], [(cola, 1)], method='card')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(SaleItem.objects.get(sale_id=response.json()['id']).vat_rate, Decimal('15.00'))
