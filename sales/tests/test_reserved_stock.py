"""G41 review: stock reserved for a trade order is not the till's to sell — online the till says how many are free,
and an offline sale replayed over reserved stock is recorded short (never refused)."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from common.tests.factories import ProductFactory, StockBatchFactory
from inventory.ledger import move, virtual_location
from inventory.models import Location
from inventory.reservations import reserve
from sales.models import SaleException
from sales.tests.test_offline_sync import OfflineSyncTestCase
from wholesale.models import SalesOrder, SalesOrderLine, TradeCustomer

D = Decimal


class ReservedStockAtTheTillTests(OfflineSyncTestCase):
    def setUp(self):
        super().setUp()
        self.customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                     legal_name='E2E Fake Bistro Ltd')
        self.orders = 0

    def hold(self, product, quantity=None, weight_kg=None):
        """Reserve ``product`` at the till's shop floor for a confirmed trade order."""
        self.orders += 1
        order = SalesOrder.objects.create(company=self.company, number=self.orders, customer=self.customer,
                                          delivery_date=timezone.localdate(), line1='1 Fake Street',
                                          town='Testtown', postcode='TT1 1TT', status=SalesOrder.CONFIRMED)
        line = SalesOrderLine.objects.create(order=order, product=product, quantity=quantity, weight_kg=weight_kg)
        batch = product.stockbatch_set.first()
        with transaction.atomic():
            self.assertFalse(reserve(line, batch.location))
        return line

    def weighed(self, catch_weight, pieces, kg):
        product = ProductFactory(company=self.company, name='Fake Weighed', price=D('0.00'), sold_by='weight',
                                 is_catch_weight=catch_weight, price_per_kg=D('10.0000'), stock_quantity=0)
        batch = StockBatchFactory(product=product, quantity=0, weight_kg=D('0'),
                                  expiration_date=timezone.localdate() + timedelta(days=5))
        with transaction.atomic():
            move(batch, pieces, 'inbound', counterpart=virtual_location(self.company, Location.SUPPLIER),
                 weight_kg=kg)
        return product

    def test_online_sale_says_how_many_are_free(self):
        self.hold(self.rice, quantity=4)  # 5 held, 4 reserved
        response = self.sell(self.session, [(self.rice, 3)], method='card')
        self.assertEqual(response.status_code, 400)
        self.assertIn('only 1 in date', str(response.json()))
        self.assertEqual(self.sell(self.session, [(self.rice, 1)], method='card').status_code, 201)

    def test_offline_each_sale_over_reserved_stock_is_recorded_short(self):
        self.hold(self.rice, quantity=4)
        [result] = self.send(self.offline([self.rice_line(3)]))
        self.assertEqual((result['status'], result['exceptions']), ('recorded', ['stock']))
        self.assertEqual(SaleException.objects.get().quantity, 2)

    def test_offline_catch_weight_sale_over_reserved_pieces_is_recorded_short(self):
        chops = self.weighed(True, 3, D('4.500'))
        self.hold(chops, quantity=2)
        line = {'product': chops.pk, 'quantity': 2, 'weight_kg': '3.000', 'charged': '30.00'}
        [result] = self.send(self.offline([line]))
        self.assertEqual((result['status'], result['exceptions']), ('recorded', ['stock']), result)
        self.assertEqual(SaleException.objects.get().quantity, 1)
        self.assertEqual(chops.stockbatch_set.get(batch_number__startswith='B').quantity, 2)  # reserved pieces kept

    def test_offline_loose_sale_over_fully_reserved_kg_is_recorded_short(self):
        mince = self.weighed(False, 0, D('1.000'))
        self.hold(mince, weight_kg=D('1.000'))
        line = {'product': mince.pk, 'weight_kg': '0.300', 'charged': '3.00'}
        [result] = self.send(self.offline([line]))
        self.assertEqual((result['status'], result['exceptions']), ('recorded', ['stock']), result)
        self.assertEqual(SaleException.objects.get().weight_kg, D('0.300'))

    def test_offline_loose_sale_reports_the_gap_against_free_kg(self):
        mince = self.weighed(False, 0, D('1.000'))
        self.hold(mince, weight_kg=D('0.800'))  # 0.200 free
        line = {'product': mince.pk, 'weight_kg': '0.500', 'charged': '5.00'}
        [result] = self.send(self.offline([line]))
        self.assertEqual(result['exceptions'], ['stock'], result)
        self.assertEqual(SaleException.objects.get().weight_kg, D('0.300'))
        self.assertEqual(mince.stockbatch_set.get(batch_number__startswith='B').weight_kg, D('0.800'))
