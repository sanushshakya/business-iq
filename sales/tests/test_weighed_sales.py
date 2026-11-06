# sales/tests/test_weighed_sales.py

"""G14: selling products by weight at the till — kg FEFO, priced weight × £/kg, voids put the kg back."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from common.tests.factories import ProductFactory, StockBatchFactory
from inventory.models import Location, StockBatch, StockMovement
from sales.models import SaleAllocation, SaleItem
from sales.tests.test_sales import SalesTestCase

D = Decimal


class WeighedSaleTests(SalesTestCase):
    def weighed(self, per_kg='8.0000', catch=False, batches=(), **extra):
        """A weighed product with one batch per ``(pieces, kg, days_to_expiry)``."""
        product = ProductFactory(company=self.company, price=D('0.00'), stock_quantity=0, sold_by='weight',
                                 is_catch_weight=catch, price_per_kg=D(per_kg), **extra)
        for number, (pieces, kg, days) in enumerate(batches):
            StockBatchFactory(product=product, quantity=pieces, weight_kg=D(kg), batch_number=f'W-{number}',
                              expiration_date=timezone.localdate() + timedelta(days=days))
        return product

    def charge(self, session_id, lines, method='card', client_ref=None, tendered=None):
        body = {'session': session_id, 'payment_method': method, 'items': lines}
        if client_ref:
            body['client_ref'] = client_ref
        if tendered:
            body['amount_tendered'] = tendered
        if method == 'card':
            body['card_auth_code'] = 'AUTH01'  # the terminal's slip (G36)
        return self.client.post(reverse('sale-list'), body, format='json')

    def kg(self, product):
        return [b.weight_kg for b in StockBatch.objects.filter(product=product).order_by('expiration_date')]

    def test_loose_sale_takes_kg_fefo_and_prices_by_weight(self):
        mince = self.weighed(per_kg='8.9900', batches=((0, '0.300', 2), (0, '5.000', 9)))
        response = self.charge(self.open()['id'], [{'product': mince.pk, 'weight_kg': '0.512'}])
        self.assertEqual(response.status_code, 201, response.content)
        line = response.json()['items'][0]
        # 0.512 × 8.99 = 4.60288 → 4.60
        self.assertEqual((line['weight_kg'], line['price_per_kg'], line['line_total'], line['quantity']),
                         ('0.512', '8.9900', '4.60', 1))
        self.assertEqual(line['unit_price'], '8.99')
        self.assertEqual(self.kg(mince), [D('0.000'), D('4.788')])
        self.assertEqual([(a.quantity, a.weight_kg) for a in SaleAllocation.objects.order_by('pk')],
                         [(0, D('0.300')), (0, D('0.212'))])

    def test_catch_weight_sale_takes_pieces_and_kg(self):
        lamb = self.weighed(per_kg='12.0000', catch=True, batches=((4, '2.000', 5),))
        response = self.charge(self.open()['id'], [{'product': lamb.pk, 'quantity': 2, 'weight_kg': '1.100'}])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['total'], '13.20')
        batch = StockBatch.objects.get(product=lamb)
        self.assertEqual((batch.quantity, batch.weight_kg), (2, D('0.900')))

    def test_short_loose_sale_books_adjustment_and_sells(self):
        rice = self.weighed(per_kg='2.0000', batches=((0, '1.000', 5),))
        response = self.charge(self.open()['id'], [{'product': rice.pk, 'weight_kg': '1.400'}])
        self.assertEqual(response.status_code, 201, response.content)
        gap = StockMovement.objects.get(source_location__type=Location.ADJUSTMENT)
        self.assertEqual(gap.weight_kg, D('0.400'))
        self.assertEqual(self.kg(rice), [D('0.000')])

    def test_no_stock_is_400_and_nothing_moves(self):
        rice = self.weighed(batches=((0, '2.000', -1),))  # expired only
        response = self.charge(self.open()['id'], [{'product': rice.pk, 'weight_kg': '0.500'}])
        self.assertEqual(response.status_code, 400)
        self.assertIn(rice.name, str(response.json()))
        self.assertFalse(SaleItem.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_too_few_pieces_is_400(self):
        lamb = self.weighed(catch=True, batches=((1, '0.500', 5),))
        response = self.charge(self.open()['id'], [{'product': lamb.pk, 'quantity': 2, 'weight_kg': '1.000'}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SaleItem.objects.exists())

    def test_line_kg_rules(self):
        session = self.open()['id']
        bread = self.stocked(name='Fake Bread')
        rice = self.weighed(batches=((0, '2.000', 5),))
        lamb = self.weighed(catch=True, batches=((2, '1.000', 5),))
        for lines in (
            [{'product': bread.pk, 'quantity': 1, 'weight_kg': '0.500'}],  # each: no kg
            [{'product': rice.pk, 'quantity': 1}],  # weighed: kg needed
            [{'product': lamb.pk, 'quantity': 1}],
            [{'product': rice.pk, 'quantity': 3, 'weight_kg': '0.500'}],  # loose: one line, not a count
            [{'product': rice.pk, 'weight_kg': '0'}],
        ):
            response = self.charge(session, lines)
            self.assertEqual(response.status_code, 400, lines)
            self.assertIn('items', response.json())
        self.assertFalse(SaleItem.objects.exists())

    def test_void_puts_the_kg_back(self):
        rice = self.weighed(batches=((0, '0.300', 2), (0, '5.000', 9)))
        lamb = self.weighed(catch=True, batches=((3, '1.500', 5),))
        session = self.open()['id']
        sale = self.charge(session, [{'product': rice.pk, 'weight_kg': '1.000'},
                                     {'product': lamb.pk, 'quantity': 1, 'weight_kg': '0.520'}]).json()
        response = self.client.post(reverse('sale-void', args=[sale['id']]), {'reason': 'Fake mistake'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.kg(rice), [D('0.300'), D('5.000')])
        batch = StockBatch.objects.get(product=lamb)
        self.assertEqual((batch.quantity, batch.weight_kg), (3, D('1.500')))

    def test_weighed_retry_does_not_double_deduct(self):
        rice = self.weighed(batches=((0, '5.000', 5),))
        session, ref = self.open()['id'], str(uuid.uuid4())
        first = self.charge(session, [{'product': rice.pk, 'weight_kg': '1.000'}], client_ref=ref)
        again = self.charge(session, [{'product': rice.pk, 'weight_kg': '1.000'}], client_ref=ref)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(self.kg(rice), [D('4.000')])

    def test_weighed_line_total_rounds_half_up(self):
        rice = self.weighed(per_kg='3.4000', batches=((0, '5.000', 5),))
        response = self.charge(self.open()['id'], [{'product': rice.pk, 'weight_kg': '0.125'}])
        self.assertEqual(response.json()['items'][0]['line_total'], '0.43')  # 0.425 → 0.43

    def test_two_weighed_lines_stay_separate(self):
        lamb = self.weighed(per_kg='10.0000', catch=True, batches=((3, '1.500', 5),))
        response = self.charge(self.open()['id'], [{'product': lamb.pk, 'quantity': 1, 'weight_kg': '0.455'},
                                                   {'product': lamb.pk, 'quantity': 1, 'weight_kg': '0.505'}])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual([i['line_total'] for i in response.json()['items']], ['4.55', '5.05'])
        self.assertEqual(StockBatch.objects.get(product=lamb).quantity, 1)

    def test_pieces_are_checked_across_lines(self):
        lamb = self.weighed(catch=True, batches=((1, '0.500', 5),))
        response = self.charge(self.open()['id'], [{'product': lamb.pk, 'quantity': 1, 'weight_kg': '0.400'},
                                                   {'product': lamb.pk, 'quantity': 1, 'weight_kg': '0.400'}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SaleItem.objects.exists())

    def test_mixed_basket_each_and_weighed_with_vat(self):
        cola = self.stocked(price='1.25', name='Fake Cola', vat_code='standard')
        olives = self.weighed(per_kg='15.0000', batches=((0, '3.000', 5),), vat_code='standard', name='Fake Olives')
        rice = self.weighed(per_kg='2.5000', batches=((0, '5.000', 5),), name='Fake Rice')
        response = self.charge(self.open()['id'], [{'product': cola.pk, 'quantity': 2},
                                                   {'product': olives.pk, 'weight_kg': '0.250'},
                                                   {'product': rice.pk, 'weight_kg': '2.000'}])
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        # 2.50 + 3.75 + 5.00; standard VAT on 6.25 gross: 2.50 → 0.42, 3.75 → 0.63
        self.assertEqual(body['total'], '11.25')
        self.assertEqual([(g['vat_code'], g['vat']) for g in body['vat_summary']],
                         [('zero', '0.00'), ('standard', '1.05')])
        self.assertEqual(StockBatch.objects.get(product=cola).quantity, 8)  # each line unchanged

    def test_weighing_has_a_ceiling(self):
        rice = self.weighed(batches=((0, '5.000', 5),))
        response = self.charge(self.open()['id'], [{'product': rice.pk, 'weight_kg': '500.000'}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(StockMovement.objects.exists())
