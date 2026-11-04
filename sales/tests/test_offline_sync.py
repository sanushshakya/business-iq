# sales/tests/test_offline_sync.py

"""
G28: sales a till made offline are replayed on the server in order, once each, and never refused: what doesn't match
(stock, price, labels, limits, a closed session) is recorded as an exception for a manager.
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from common.models import Setting
from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.ledger import move, virtual_location
from inventory.models import Location, Product, StockBatch, StockMovement
from sales import offline_sync
from sales.models import Sale, SaleException, TillSession
from sales.services import session_summary
from sales.tests.test_labels import gtin
from sales.tests.test_sales import SalesTestCase, make_till
from sync.models import OutboundEvent, StorefrontConnection
from vat.models import VatRate

D = Decimal


class OfflineSyncTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.session = self.open()['id']
        self.rice = self.stocked(price='2.50', batches=((5, 30),), name='Fake Rice')
        self.mince = ProductFactory(company=self.company, name='Fake Mince', price=D('0.00'), sold_by='weight',
                                    price_per_kg=D('8.9900'), stock_quantity=0)
        self.n = 0

    def offline(self, items, *, minutes_ago=10, method='cash', tendered=None, **extra):
        self.n += 1
        total = sum(D(item['charged']) for item in items)
        sale = {
            'client_ref': str(uuid.uuid4()),
            'offline_number': f'HS-T01-ABC-{self.n:06d}',
            'created_at': (timezone.now() - timedelta(minutes=minutes_ago)).isoformat(),
            'session': self.session,
            'cashier': self.cashier.pk,
            'payment_method': method,
            'items': items,
            **extra,
        }
        if method == 'cash':
            sale['amount_tendered'] = tendered or f'{total:.2f}'
        return sale

    def send(self, *sales, client=None):
        response = (client or self.client).post(reverse('offline-sales'), {'sales': list(sales)}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['results']

    def rice_line(self, quantity=1, charged=None):
        return {'product': self.rice.pk, 'quantity': quantity, 'charged': charged or f'{D("2.50") * quantity:.2f}'}

    def stock(self, product):
        return Product.objects.get(pk=product.pk).stock_quantity

    def kinds(self, sale_id):
        return sorted(SaleException.objects.filter(sale_id=sale_id).values_list('kind', flat=True))


class ReplayTests(OfflineSyncTestCase):
    def test_replaying_twice_deducts_once(self):
        sale = self.offline([self.rice_line(2)])
        [first] = self.send(sale)
        [again] = self.send(sale)
        self.assertEqual((first['status'], again['status']), ('recorded', 'duplicate'))
        self.assertEqual(first['sale'], again['sale'])
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(self.stock(self.rice), 3)
        recorded = Sale.objects.get()
        self.assertEqual((recorded.offline_number, recorded.total, recorded.payment_method),
                         ('HS-T01-ABC-000001', D('5.00'), 'cash'))
        self.assertIsNotNone(recorded.sold_at)

    def test_online_attempt_already_recorded_is_duplicate(self):
        ref = str(uuid.uuid4())
        self.assertEqual(self.sell(self.session, [(self.rice, 1)], method='card', client_ref=ref).status_code, 201)
        [result] = self.send(self.offline([self.rice_line()], client_ref=ref, method='card'))
        self.assertEqual(result['status'], 'duplicate')
        self.assertEqual(self.stock(self.rice), 4)

    def test_out_of_order_arrival(self):
        older = self.offline([self.rice_line()], minutes_ago=30)
        newer = self.offline([self.rice_line()], minutes_ago=5)
        oldest = self.offline([self.rice_line()], minutes_ago=60)
        results = self.send(newer, older)  # arrives newest first
        self.assertEqual([r['client_ref'] for r in results], [older['client_ref'], newer['client_ref']])
        first_two = [Sale.objects.get(pk=r['sale']) for r in results]
        self.assertLess(first_two[0].pk, first_two[1].pk)  # applied oldest first
        [late] = self.send(oldest)  # an older sale arriving after newer ones is still recorded
        self.assertEqual(late['status'], 'recorded')
        self.assertEqual(self.stock(self.rice), 2)

    def test_session_totals_include_synced_sales(self):
        self.send(self.offline([self.rice_line(2)]), self.offline([self.rice_line()], method='card'))
        summary = session_summary(TillSession.objects.get(pk=self.session))
        self.assertEqual((summary['sales_count'], summary['cash_total'], summary['card_total']),
                         (2, D('5.00'), D('2.50')))

    def test_card_auth_code_and_loyalty_kept(self):
        [result] = self.send(self.offline([self.rice_line()], method='card', card_auth_code='A1B2C3',
                                          loyalty_barcode='9900012345'))
        sale = Sale.objects.get(pk=result['sale'])
        self.assertEqual((sale.card_auth_code, sale.loyalty_barcode, sale.amount_tendered),
                         ('A1B2C3', '9900012345', D('2.50')))


class ExceptionTests(OfflineSyncTestCase):
    def test_shortfall_creates_exception(self):
        [result] = self.send(self.offline([self.rice_line(8)]))  # 5 in stock
        self.assertEqual((result['status'], result['exceptions']), ('recorded', ['stock']))
        exception = SaleException.objects.get()
        self.assertEqual((exception.kind, exception.product, exception.quantity), ('stock', self.rice, 3))
        short = StockBatch.objects.get(batch_number__startswith='SHORT-')
        self.assertEqual(short.quantity, 0)  # nothing ever goes below 0
        self.assertEqual(StockBatch.objects.filter(product=self.rice).exclude(pk=short.pk).get().quantity, 0)
        moves = StockMovement.objects.filter(batch__product=self.rice, movement_type='outbound')
        self.assertEqual(sum(m.quantity for m in moves), 8)
        self.assertEqual(self.stock(self.rice), 0)

    def test_price_difference_is_an_exception_and_charged_wins(self):
        [result] = self.send(self.offline([self.rice_line(2, charged='4.00')]))  # today's price says 5.00
        sale = Sale.objects.get(pk=result['sale'])
        self.assertEqual((sale.total, result['exceptions']), (D('4.00'), ['price']))
        exception = SaleException.objects.get()
        self.assertEqual((exception.expected, exception.charged, exception.item.line_total),
                         (D('5.00'), D('4.00'), D('4.00')))

    def test_vat_uses_the_sale_date_rate(self):
        cola = self.stocked(price='1.20', name='Fake Cola', vat_code='standard')
        sold_on = (timezone.now() - timedelta(days=3))
        VatRate.objects.create(code='standard', rate=D('25.00'), valid_from=(sold_on + timedelta(days=1)).date())
        sale = self.offline([{'product': cola.pk, 'quantity': 1, 'charged': '1.20'}])
        sale['created_at'] = sold_on.isoformat()
        [result] = self.send(sale)
        item = Sale.objects.get(pk=result['sale']).items.get()
        self.assertEqual((item.vat_rate, item.vat_amount, item.net_amount), (D('20.00'), D('0.20'), D('1.00')))

    def test_typed_weight_over_limit_kept_with_exception(self):
        StockBatchFactory(product=self.mince, quantity=0, weight_kg=D('40.000'),
                          expiration_date=timezone.localdate() + timedelta(days=5))
        Setting.objects.create(company=self.company, key='manual_weight_max_kg', value='2')
        [result] = self.send(self.offline([{'product': self.mince.pk, 'weight_kg': '3.000', 'charged': '26.97'}]))
        self.assertEqual(result['exceptions'], ['weight_limit'])
        item = Sale.objects.get(pk=result['sale']).items.get()
        self.assertEqual((item.weight_kg, item.line_total, item.manual_weight), (D('3.000'), D('26.97'), True))

    def test_label_unreadable_kept_with_exception(self):
        StockBatchFactory(product=self.mince, quantity=0, weight_kg=D('2.000'),
                          expiration_date=timezone.localdate() + timedelta(days=5))
        line = {'barcode': '2199999000000', 'product': self.mince.pk, 'weight_kg': '0.500', 'charged': '4.50'}
        [result] = self.send(self.offline([line]))
        self.assertEqual((result['status'], result['exceptions']), ('recorded', ['label']))
        item = Sale.objects.get(pk=result['sale']).items.get()
        self.assertEqual((item.product, item.weight_kg, item.line_total, item.barcode),
                         (self.mince, D('0.500'), D('4.50'), '2199999000000'))

    def test_loose_kg_shortfall(self):
        [result] = self.send(self.offline([{'product': self.mince.pk, 'weight_kg': '0.750', 'charged': '6.74'}]))
        self.assertEqual(result['exceptions'], ['stock'])
        exception = SaleException.objects.get()
        self.assertEqual((exception.weight_kg, exception.quantity), (D('0.750'), None))

    def test_catch_weight_shortfall(self):
        chops = ProductFactory(company=self.company, name='Fake Chops', price=D('0.00'), sold_by='weight',
                               is_catch_weight=True, price_per_kg=D('10.0000'), stock_quantity=0)
        batch = StockBatchFactory(product=chops, quantity=0, weight_kg=D('0'),
                                  expiration_date=timezone.localdate() + timedelta(days=5))
        with transaction.atomic():
            move(batch, 2, 'inbound', counterpart=virtual_location(self.company, Location.SUPPLIER),
                 weight_kg=D('0.800'))
        line = {'product': chops.pk, 'quantity': 3, 'weight_kg': '1.200', 'charged': '12.00'}
        [result] = self.send(self.offline([line]))
        self.assertEqual(result['exceptions'], ['stock'])
        item = Sale.objects.get(pk=result['sale']).items.get()
        allocations = list(item.allocations.order_by('pk').values_list('quantity', 'weight_kg'))
        self.assertEqual(sum(q for q, _ in allocations), 3)
        self.assertEqual(sum(kg for _, kg in allocations), D('1.200'))
        self.assertEqual(SaleException.objects.get().quantity, 1)
        batch.refresh_from_db()
        self.assertEqual((batch.quantity, batch.weight_kg >= 0), (0, True))

    def test_session_closed_meanwhile(self):
        TillSession.objects.filter(pk=self.session).update(closed_at=timezone.now(), closed_by=self.manager)
        [result] = self.send(self.offline([self.rice_line()]))
        self.assertEqual((result['status'], result['exceptions']), ('recorded', ['session_closed']))
        self.assertEqual(Sale.objects.get().session_id, self.session)

    def test_voided_offline_on_closed_session(self):
        TillSession.objects.filter(pk=self.session).update(closed_at=timezone.now(), closed_by=self.manager)
        void = {'reason': 'Fake: wrong item', 'voided_at': timezone.now().isoformat(), 'voided_by': self.cashier.pk}
        [result] = self.send(self.offline([self.rice_line(2)], void=void))
        sale = Sale.objects.get(pk=result['sale'])
        self.assertEqual((sale.status, sale.void_reason, sale.voided_by), ('voided', 'Fake: wrong item', self.cashier))
        self.assertEqual(self.stock(self.rice), 5)  # back in its batch

    def test_unknown_cashier_is_the_sender_with_an_exception(self):
        stranger = UserFactory(company=CompanyFactory())
        [result] = self.send(self.offline([self.rice_line()], cashier=stranger.pk))
        self.assertEqual(result['exceptions'], ['cashier'])
        self.assertEqual(Sale.objects.get().cashier, self.cashier)


class RefusalTests(OfflineSyncTestCase):
    def test_other_company_session_refused(self):
        their_till = make_till(CompanyFactory())
        theirs = TillSession.objects.create(till=their_till, opened_by=self.manager, opening_float=D('0'))
        [result] = self.send(self.offline([self.rice_line()], session=theirs.pk))
        self.assertEqual(result['status'], 'error')
        self.assertFalse(Sale.objects.exists())
        self.assertEqual(self.stock(self.rice), 5)

    def test_other_company_product_refused(self):
        theirs = ProductFactory(company=CompanyFactory())
        [result] = self.send(self.offline([{'product': theirs.pk, 'quantity': 1, 'charged': '1.00'}]))
        self.assertEqual(result['status'], 'error')
        self.assertFalse(Sale.objects.exists())

    def test_one_error_does_not_stop_others(self):
        VatRate.objects.filter(code='reduced').delete()
        odd = self.stocked(price='1.00', name='Fake Odd', vat_code='reduced')  # no VAT rate for it now
        bad = self.offline([{'product': odd.pk, 'quantity': 1, 'charged': '1.00'}])
        good = self.offline([self.rice_line()])
        results = {r['client_ref']: r for r in self.send(bad, good)}
        self.assertEqual(results[bad['client_ref']]['status'], 'error')
        self.assertIn('VAT', results[bad['client_ref']]['detail'])
        self.assertEqual(results[good['client_ref']]['status'], 'recorded')
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(self.stock(odd), 10)

    def test_too_many_sales_is_400(self):
        sales = [self.offline([self.rice_line()]) for _ in range(101)]
        response = self.client.post(reverse('offline-sales'), {'sales': sales}, format='json')
        self.assertEqual(response.status_code, 400)


class ReviewFixTests(OfflineSyncTestCase):
    """G28 review: never block the queue, never lose a void, keep the till's reading."""

    def setUp(self):
        super().setUp()
        self.connection = StorefrontConnection.objects.create(company=self.company, base_url='https://m18.example.com/',
                                                              webhook_secret='test-webhook-secret')
        self.enterContext(mock.patch('sync.outbox.deliver_outbound_event.apply_async'))

    def events(self):
        return list(OutboundEvent.objects.order_by('pk').values_list('event_type', flat=True))

    def test_gs1_label_without_price_on_an_each_product(self):
        Product.objects.filter(pk=self.rice.pk).update(barcode=gtin('0501234567890')[1:])
        line = {'barcode': '01' + gtin('0501234567890') + '10LOT1', 'product': self.rice.pk, 'charged': '2.50'}
        [result] = self.send(self.offline([line]))
        self.assertEqual((result['status'], result['exceptions']), ('recorded', []))
        self.assertEqual(self.stock(self.rice), 4)

    def test_an_unexpected_error_is_an_error_result_and_the_rest_go_through(self):
        first, second = self.offline([self.rice_line()], minutes_ago=20), self.offline([self.rice_line()])
        real = offline_sync.record_offline_sale
        calls = []

        def flaky(user, data):
            calls.append(data['client_ref'])
            if len(calls) == 1:
                raise RuntimeError('Fake: disk full')
            return real(user, data)

        with mock.patch('sales.offline_sync.record_offline_sale', side_effect=flaky):
            results = self.send(first, second)
        self.assertEqual([r['status'] for r in results], ['error', 'recorded'])
        self.assertNotIn('disk', results[0]['detail'])  # no internals to the till
        self.assertEqual(Sale.objects.count(), 1)

    def test_same_offline_number_twice_is_an_error_not_a_crash(self):
        one = self.offline([self.rice_line()], minutes_ago=20)
        two = self.offline([self.rice_line()], offline_number=one['offline_number'])
        results = self.send(one, two)
        self.assertEqual([r['status'] for r in results], ['recorded', 'error'])

    def test_void_of_a_sale_already_on_the_server_is_applied(self):
        ref = str(uuid.uuid4())
        self.assertEqual(self.sell(self.session, [(self.rice, 1)], method='card', client_ref=ref).status_code, 201)
        void = {'reason': 'Fake: wrong item', 'voided_at': timezone.now().isoformat(), 'voided_by': self.cashier.pk}
        [result] = self.send(self.offline([self.rice_line()], client_ref=ref, method='card', void=void))
        self.assertEqual(result['status'], 'duplicate')
        sale = Sale.objects.get()
        self.assertEqual((sale.status, sale.void_reason), ('voided', 'Fake: wrong item'))
        self.assertEqual(self.stock(self.rice), 5)

    def test_void_of_an_online_sale_with_loyalty_reverses_its_points(self):
        ref = str(uuid.uuid4())
        body = {'session': self.session, 'payment_method': 'card', 'client_ref': ref, 'loyalty_barcode': '9900012345',
                'items': [{'product': self.rice.pk, 'quantity': 1}]}
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(reverse('sale-list'), body, format='json').status_code, 201)
        void = {'reason': 'Fake', 'voided_at': timezone.now().isoformat(), 'voided_by': self.cashier.pk}
        self.send(self.offline([self.rice_line()], client_ref=ref, method='card', void=void))
        self.assertEqual(self.events(), ['loyalty.award', 'loyalty.reverse'])

    def test_sale_voided_offline_moves_no_stock_even_when_short(self):
        void = {'reason': 'Fake', 'voided_at': timezone.now().isoformat(), 'voided_by': self.cashier.pk}
        [result] = self.send(self.offline([self.rice_line(8)], void=void, loyalty_barcode='9900012345'))
        self.assertEqual((result['status'], result['exceptions']), ('recorded', []))
        self.assertEqual(Sale.objects.get().status, 'voided')
        self.assertEqual(self.stock(self.rice), 5)
        self.assertFalse(StockMovement.objects.filter(batch__product=self.rice, movement_type='outbound').exists())
        self.assertFalse(StockBatch.objects.filter(batch_number__startswith='SHORT-').exists())
        self.assertEqual(self.events(), [])  # no award, no reverse

    def test_loyalty_awarded_for_a_synced_sale(self):
        self.send(self.offline([self.rice_line()], loyalty_barcode='9900012345'))
        self.assertEqual(self.events(), ['loyalty.award'])

    def test_product_now_sold_by_weight_is_kept_with_an_exception(self):
        Product.objects.filter(pk=self.rice.pk).update(sold_by='weight', price_per_kg=D('5.0000'))
        [result] = self.send(self.offline([self.rice_line(2)]))
        # no kg was recorded for it, so no stock could be taken: a manager counts it
        self.assertEqual((result['status'], result['exceptions']), ('recorded', ['product_changed', 'stock']))
        self.assertEqual(Sale.objects.get().total, D('5.00'))

    def test_weighed_product_without_price_per_kg_is_kept(self):
        Product.objects.filter(pk=self.mince.pk).update(price_per_kg=None)
        [result] = self.send(self.offline([{'product': self.mince.pk, 'weight_kg': '0.500', 'charged': '4.50'}]))
        self.assertEqual(result['status'], 'recorded')
        item = Sale.objects.get().items.get()
        self.assertEqual((item.line_total, item.unit_price), (D('4.50'), D('4.50')))

    def test_label_now_reading_as_another_product_keeps_the_tills_reading(self):
        StockBatchFactory(product=self.mince, quantity=0, weight_kg=D('2.000'),
                          expiration_date=timezone.localdate() + timedelta(days=5))
        Product.objects.filter(pk=self.rice.pk).update(barcode=gtin('0501234567890')[1:])  # the label is rice now
        line = {'barcode': '01' + gtin('0501234567890') + '10LOT1', 'product': self.mince.pk, 'weight_kg': '0.500',
                'charged': '4.50'}
        [result] = self.send(self.offline([line]))
        self.assertEqual(result['exceptions'], ['label'])
        item = Sale.objects.get().items.get()
        self.assertEqual((item.product, item.weight_kg, item.line_total), (self.mince, D('0.500'), D('4.50')))
        self.assertEqual(self.stock(self.rice), 5)

    def test_unit_price_matches_the_charged_line(self):
        [result] = self.send(self.offline([self.rice_line(2, charged='4.00')]))
        item = Sale.objects.get().items.get()
        self.assertEqual((item.unit_price, item.line_total), (D('2.00'), D('4.00')))

    def test_cash_short_of_the_total_is_an_exception(self):
        [result] = self.send(self.offline([self.rice_line(2)], tendered='4.00'))
        self.assertEqual(result['exceptions'], ['tender'])

    def test_client_ref_seen_on_another_session_of_the_company_is_a_duplicate(self):
        sale = self.offline([self.rice_line()])
        self.send(sale)
        TillSession.objects.filter(pk=self.session).update(closed_at=timezone.now(), closed_by=self.manager)
        other = self.open()['id']
        [again] = self.send({**sale, 'session': other})
        self.assertEqual(again['status'], 'duplicate')
        self.assertEqual(Sale.objects.count(), 1)

    def test_offline_number_is_required(self):
        response = self.client.post(reverse('offline-sales'), {'sales': [self.offline([self.rice_line()],
                                                                                      offline_number='')]},
                                    format='json')
        self.assertEqual(response.status_code, 400)
