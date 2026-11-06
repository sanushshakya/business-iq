"""G41: reserving warehouse stock for trade orders — FEFO, short, released; other takes skip reserved stock."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.ledger import LedgerError, move, take_fefo, take_weighed, transfer_fefo, virtual_location
from inventory.models import Location, Product, StockBatch, StockMovement, StockReservation
from inventory.reservations import release, reserve, reserved_amounts
from sync.models import StockSyncState, StorefrontConnection
from sync.stock import online_available
from wholesale.models import SalesOrder, SalesOrderLine, TradeCustomer

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.company = CompanyFactory()
        self.warehouse = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse',
                                                 type=Location.WAREHOUSE)
        self.shop = Location.objects.create(company=self.company, code='SHOP-X', name='Fake shop',
                                            type=Location.SHOP_FLOOR, sellable=True)
        self.customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                     legal_name='E2E Fake Bistro Ltd')
        self.user = UserFactory(company=self.company, is_staff=True)
        self.each = ProductFactory(company=self.company, stock_quantity=0)
        self.loose = ProductFactory(company=self.company, stock_quantity=0, sold_by=Product.WEIGHT,
                                    price_per_kg=D('9.99'))
        self.catch = ProductFactory(company=self.company, stock_quantity=0, sold_by=Product.WEIGHT,
                                    is_catch_weight=True, price_per_kg=D('9.99'), nominal_weight_kg=D('1.500'))
        self.number = 0

    def batch(self, product, quantity=0, days=30, kg=None, location=None, number=None):
        """A batch received through the ledger (so movements balance)."""
        batch = StockBatchFactory(product=product, quantity=0, weight_kg=D('0'), location=location or self.warehouse,
                                  expiration_date=self.today + timedelta(days=days),
                                  **({'batch_number': number} if number else {}))
        if quantity or kg:
            with transaction.atomic():
                move(batch, quantity, 'inbound', counterpart=virtual_location(self.company, 'supplier'),
                     weight_kg=kg if product.is_weighed else None)
        batch.refresh_from_db()
        return batch

    def line(self, product, quantity=None, weight_kg=None):
        self.number += 1
        order = SalesOrder.objects.create(company=self.company, number=self.number, customer=self.customer,
                                          delivery_date=self.today, line1='1 Fake Street', town='Testtown',
                                          postcode='TT1 1TT', created_by=self.user, status=SalesOrder.CONFIRMED)
        return SalesOrderLine.objects.create(order=order, product=product, quantity=quantity, weight_kg=weight_kg)

    def reserve(self, line, location=None):
        with transaction.atomic():
            return reserve(line, location or self.warehouse)

    def held(self, line):
        return reserved_amounts([line])[line.pk]

    def by_batch(self, line):
        rows = StockReservation.objects.filter(order_line=line, released_at__isnull=True).order_by('batch_id')
        return [(r.batch.batch_number, r.quantity, r.weight_kg) for r in rows]


class ReserveTests(Base):
    def test_fefo_across_batches_skipping_expired_and_other_locations(self):
        self.batch(self.each, 5, days=-1, number='EXPIRED')
        self.batch(self.each, 4, days=20, number='LATER')
        self.batch(self.each, 3, days=10, number='SOONER')
        self.batch(self.each, 50, days=5, location=self.shop, number='AT-SHOP')
        line = self.line(self.each, quantity=5)
        movements = StockMovement.objects.count()
        self.assertEqual(self.reserve(line), 0)
        self.assertEqual(self.by_batch(line), [('LATER', 2, None), ('SOONER', 3, None)])
        self.assertEqual(self.held(line), 5)
        self.assertEqual(StockMovement.objects.count(), movements)  # a reservation is not a movement
        self.assertEqual(StockBatch.objects.get(batch_number='SOONER').quantity, 3)

    def test_short_then_top_up(self):
        self.batch(self.each, 3)
        line = self.line(self.each, quantity=5)
        self.assertEqual(self.reserve(line), 2)
        self.assertEqual(self.held(line), 3)
        self.assertEqual(self.reserve(line), 2)  # nothing new: no double reservation
        self.assertEqual(StockReservation.objects.filter(order_line=line).aggregate(s=Sum('quantity'))['s'], 3)
        self.batch(self.each, 10, days=40)
        self.assertEqual(self.reserve(line), 0)
        self.assertEqual(self.held(line), 5)

    def test_two_orders_never_reserve_more_than_a_batch_holds(self):
        self.batch(self.each, 4)
        first, second = self.line(self.each, quantity=3), self.line(self.each, quantity=3)
        self.assertEqual((self.reserve(first), self.reserve(second)), (0, 2))
        self.assertEqual((self.held(first), self.held(second)), (3, 1))

    def test_loose_kg(self):
        self.batch(self.loose, kg=D('2.500'), days=10, number='K1')
        self.batch(self.loose, kg=D('4.000'), days=20, number='K2')
        line = self.line(self.loose, weight_kg=D('3.250'))
        self.assertEqual(self.reserve(line), D('0'))
        self.assertEqual(self.by_batch(line), [('K1', 0, D('2.500')), ('K2', 0, D('0.750'))])
        self.assertEqual(self.held(line), D('0.750') + D('2.500'))

    def test_catch_weight_pieces(self):
        self.batch(self.catch, 2, kg=D('3.100'), days=10, number='P1')
        self.batch(self.catch, 5, kg=D('7.400'), days=20, number='P2')
        line = self.line(self.catch, quantity=4)
        self.assertEqual(self.reserve(line), 0)
        self.assertEqual(self.by_batch(line), [('P1', 2, None), ('P2', 2, None)])

    def test_release(self):
        self.batch(self.each, 5)
        line = self.line(self.each, quantity=5)
        self.reserve(line)
        with transaction.atomic():
            release(line.order)
        self.assertEqual(self.held(line), 0)
        self.assertTrue(StockReservation.objects.filter(order_line=line, released_at__isnull=False).exists())
        with transaction.atomic():
            release(line.order)  # nothing left to release: fine


class OtherTakesSkipReservedTests(Base):
    def setUp(self):
        super().setUp()
        self.reserved = self.batch(self.each, 5, days=10, number='RES')
        self.reserve(self.line(self.each, quantity=4))

    def test_take_fefo_takes_only_free_units(self):
        self.batch(self.each, 3, days=20, number='FREE')
        with transaction.atomic():
            allocations, short = take_fefo(self.each, 6, locations=[self.warehouse],
                                           destination=virtual_location(self.company, 'customer'))
        self.assertEqual([(b.batch_number, n) for b, n in allocations], [('RES', 1), ('FREE', 3)])
        self.assertEqual(short, 2)
        self.reserved.refresh_from_db()
        self.assertEqual(self.reserved.quantity, 4)  # the reserved 4 are still there

    def test_transfer_is_short_rather_than_take_reserved(self):
        with transaction.atomic(), self.assertRaises(LedgerError) as caught:
            transfer_fefo(self.each, 2, source=self.warehouse, to=self.shop)
        self.assertEqual(caught.exception.available, 1)
        with transaction.atomic():
            transfer_fefo(self.each, 1, source=self.warehouse, to=self.shop)
        self.reserved.refresh_from_db()
        self.assertEqual(self.reserved.quantity, 4)

    def test_loose_gap_never_eats_reserved_kg(self):
        batch = self.batch(self.loose, kg=D('2.000'), number='LOOSE')
        self.reserve(self.line(self.loose, weight_kg=D('1.500')))
        with transaction.atomic():
            take_weighed(self.loose, quantity=0, weight_kg=D('0.800'), locations=[self.warehouse],
                         destination=virtual_location(self.company, 'customer'))
        batch.refresh_from_db()
        self.assertEqual(batch.weight_kg, D('1.500'))  # 0.5 free + 0.3 booked from ADJUSTMENT went out

    def test_loose_all_reserved_is_no_stock(self):
        self.batch(self.loose, kg=D('1.000'), number='ALL')
        self.reserve(self.line(self.loose, weight_kg=D('1.000')))
        with transaction.atomic(), self.assertRaises(LedgerError):
            take_weighed(self.loose, quantity=0, weight_kg=D('0.100'), locations=[self.warehouse],
                         destination=virtual_location(self.company, 'customer'))

    def test_catch_weight_takes_free_pieces_and_keeps_the_real_count(self):
        batch = self.batch(self.catch, 3, kg=D('4.500'), number='CW')
        self.reserve(self.line(self.catch, quantity=2))
        with transaction.atomic(), self.assertRaises(LedgerError) as caught:
            take_weighed(self.catch, quantity=2, weight_kg=None, locations=[self.warehouse],
                         destination=virtual_location(self.company, 'customer'))
        self.assertEqual(caught.exception.available, 1)
        with transaction.atomic():
            take_weighed(self.catch, quantity=1, weight_kg=None, locations=[self.warehouse],
                         destination=virtual_location(self.company, 'customer'))
        batch.refresh_from_db()
        self.assertEqual((batch.quantity, batch.weight_kg), (2, D('3.000')))  # average kg, not "the last piece"

    def test_a_write_off_trims_the_newest_reservation_first(self):
        older = StockReservation.objects.get(batch=self.reserved)
        newer_line = self.line(self.each, quantity=1)
        self.reserve(newer_line)  # the 5th unit
        with transaction.atomic():
            move(StockBatch.objects.select_for_update().get(pk=self.reserved.pk), 2, 'outbound',
                 counterpart=virtual_location(self.company, 'waste'))
        older.refresh_from_db()
        self.assertEqual(self.held(newer_line), 0)
        self.assertEqual(older.quantity, 3)
        self.assertTrue(StockReservation.objects.get(order_line=newer_line).released_at)

    def test_a_trim_never_revives_a_released_reservation(self):
        # The trim locks the active rows and re-reads them, so one released meanwhile (a cancel) stays released.
        reservation = StockReservation.objects.get(batch=self.reserved)
        with transaction.atomic():
            release(reservation.order_line.order)
            move(StockBatch.objects.select_for_update().get(pk=self.reserved.pk), 4, 'outbound',
                 counterpart=virtual_location(self.company, 'waste'))
        reservation.refresh_from_db()
        self.assertIsNotNone(reservation.released_at)
        self.assertEqual(reservation.quantity, 4)  # released, not trimmed

    def test_movements_still_balance(self):
        with transaction.atomic():
            take_fefo(self.each, 1, locations=[self.warehouse], destination=virtual_location(self.company, 'customer'))
        inflow = StockMovement.objects.filter(batch=self.reserved, movement_type='inbound').aggregate(
            s=Sum('quantity'))['s']
        outflow = StockMovement.objects.filter(batch=self.reserved, movement_type='outbound').aggregate(
            s=Sum('quantity'))['s']
        self.reserved.refresh_from_db()
        self.assertEqual(inflow - outflow, self.reserved.quantity)


class OnlineTests(Base):
    def test_online_available_excludes_reserved_stock(self):
        Location.objects.filter(pk=self.warehouse.pk).update(sellable=True, online_sellable=True)
        self.batch(self.each, 10)
        product = Product.objects.get(pk=self.each.pk)
        self.assertEqual(online_available([product])[product.pk], 10)
        line = self.line(self.each, quantity=4)
        self.reserve(line)
        self.assertEqual(online_available([product])[product.pk], 6)
        with transaction.atomic():
            release(line.order)
        self.assertEqual(online_available([product])[product.pk], 10)

    def test_reserving_marks_the_product_for_the_storefront(self):
        StorefrontConnection.objects.create(company=self.company, base_url='https://example.test', webhook_secret='s')
        Product.objects.filter(pk=self.each.pk).update(external_id='m18-1')
        self.batch(self.each, 10)
        StockSyncState.objects.all().delete()
        self.reserve(self.line(self.each, quantity=4))
        self.assertTrue(StockSyncState.objects.get(product=self.each).pending)


class ReservationAdminTests(Base):
    def test_view_only(self):
        from django.contrib import admin

        model_admin = admin.site._registry[StockReservation]
        request = type('Request', (), {'user': UserFactory(is_staff=True, is_superuser=True)})()
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
