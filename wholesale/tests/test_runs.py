"""G44: delivery runs — planned, out, completed; stock moves DISPATCH -> van -> customer; proof of delivery."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.ledger import dispatch_location, move, virtual_location
from inventory.models import Location, Product, StockBatch, StockMovement
from tenants.models import Company
from tenants.roles import DRIVER
from wholesale import invoices
from wholesale.invoices import create_invoice, issue
from wholesale.models import DeliveryRun, DeliveryStop, SalesOrder, SalesOrderLine, TradeCustomer
from wholesale.orders import confirm, next_order_number
from wholesale.picking import finish_pick, record_pick, start_pick
from wholesale.runs import RunError, cancel_run, complete_run, create_run, deliver, fail_stop, set_stops, start_run

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.company = CompanyFactory()
        Company.objects.filter(pk=self.company.pk).update(vat_number='GB123456789')
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.driver, _code = get_user_model().objects.create_operator(self.company, 'Fake Driver', DRIVER)
        self.warehouse = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse',
                                                 type=Location.WAREHOUSE)
        self.van = Location.objects.create(company=self.company, code='VAN-1', name='Fake van', type=Location.VAN)
        self.customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                     legal_name='E2E Fake Bistro Ltd')
        self.rice = ProductFactory(company=self.company, name='Fake Rice', stock_quantity=0,
                                   wholesale_price=D('2.0000'))
        self.chops = ProductFactory(company=self.company, name='Fake Chops', stock_quantity=0, sold_by=Product.WEIGHT,
                                    is_catch_weight=True, price_per_kg=D('9.99'), nominal_weight_kg=D('0.500'),
                                    wholesale_price=D('6.0000'))
        self.stock(self.rice, 20, None, 'RICE-1')
        self.stock(self.chops, 10, D('5.100'), 'CHOP-1')

    def stock(self, product, quantity, kg, number):
        batch = StockBatchFactory(product=product, quantity=0, weight_kg=D('0'), location=self.warehouse,
                                  batch_number=number, expiration_date=self.today + timedelta(days=20))
        with transaction.atomic():
            move(batch, quantity, 'inbound', counterpart=virtual_location(self.company, 'supplier'),
                 weight_kg=kg if product.is_weighed else None)

    def picked_order(self, rice=3, chops=2, chops_kg=D('1.040')):
        with transaction.atomic():
            order = SalesOrder.objects.create(
                company=self.company, number=next_order_number(self.company), customer=self.customer,
                delivery_date=self.today, line1='1 Fake Street', town='Testtown', postcode='TT1 1TT',
                created_by=self.staff, source_location=self.warehouse)
        SalesOrderLine.objects.create(order=order, product=self.rice, quantity=rice)
        SalesOrderLine.objects.create(order=order, product=self.chops, quantity=chops)
        confirm(order, self.staff)
        start_pick(order)
        record_pick(order, order.lines.get(product=self.rice).pk, batch_number='RICE-1', quantity=rice,
                    user=self.staff)
        record_pick(order, order.lines.get(product=self.chops).pk, batch_number='CHOP-1', quantity=chops,
                    weight_kg=chops_kg, user=self.staff)
        finish_pick(order, self.staff)
        order.refresh_from_db()
        return order

    def plan(self, *orders):
        return create_run(self.company, self.staff, run_date=self.today, vehicle=self.van, driver=self.driver,
                          orders=list(orders))

    def held(self, location, product):
        totals = StockBatch.objects.filter(location=location, product=product).aggregate(
            units=Sum('quantity'), kg=Sum('weight_kg'))
        return totals['units'] or 0, totals['kg'] or D('0')

    def status(self, order):
        return SalesOrder.objects.get(pk=order.pk).status

    def assert_balances(self):
        for batch in StockBatch.objects.filter(product__company=self.company):
            moved = StockMovement.objects.filter(batch=batch)

            def total(kind, field):
                return moved.filter(movement_type=kind).aggregate(s=Sum(field))['s'] or 0

            self.assertEqual(total('inbound', 'quantity') - total('outbound', 'quantity'), batch.quantity)
            if batch.product.is_weighed:
                self.assertEqual(total('inbound', 'weight_kg') - total('outbound', 'weight_kg'), batch.weight_kg)


class RunStatusTests(Base):
    def test_plan_start_deliver_complete(self):
        first, second = self.picked_order(), self.picked_order(rice=1, chops=1, chops_kg=D('0.520'))
        run = self.plan(second, first)
        self.assertEqual((run.status, run.number_display), ('planned', 'RUN-000001'))
        self.assertEqual([stop.order for stop in run.stops.order_by('position')], [second, first])
        dispatch = dispatch_location(self.company)

        start_run(run, self.staff)
        run.refresh_from_db()
        self.assertEqual(run.status, 'out')
        self.assertEqual((self.status(first), self.status(second)), ('dispatched', 'dispatched'))
        self.assertEqual(self.held(self.van, self.rice), (4, D('0')))
        self.assertEqual(self.held(self.van, self.chops), (3, D('1.560')))
        self.assertEqual(self.held(dispatch, self.rice)[0], 0)

        stop = run.stops.get(order=first)
        deliver(stop, self.driver, received_by='Fake Chef', notes='Back door')
        stop.refresh_from_db()
        self.assertEqual((stop.status, stop.received_by, stop.notes, stop.recorded_by),
                         ('delivered', 'Fake Chef', 'Back door', self.driver))
        self.assertIsNotNone(stop.delivered_at)
        self.assertEqual(self.status(first), 'delivered')
        self.assertEqual(self.held(self.van, self.chops), (1, D('0.520')))
        customer = virtual_location(self.company, 'customer')
        self.assertEqual(StockMovement.objects.filter(destination_location=customer, batch__product=self.rice)
                         .aggregate(s=Sum('quantity'))['s'], 3)

        with self.assertRaises(RunError):
            complete_run(run, self.staff)  # a stop is still pending
        deliver(run.stops.get(order=second), self.staff, received_by='Fake Porter')
        complete_run(run, self.staff)
        run.refresh_from_db()
        self.assertEqual(run.status, 'completed')
        self.assertEqual(self.held(self.van, self.rice), (0, D('0')))
        self.assert_balances()

    def test_refused_moves(self):
        order = self.picked_order()
        run = self.plan(order)
        for step in (lambda: complete_run(run, self.staff),
                     lambda: deliver(run.stops.get(), self.staff, received_by='x'),
                     lambda: fail_stop(run.stops.get(), self.staff, 'x')):
            with self.assertRaises(RunError):
                step()
        start_run(run, self.staff)
        for step in (lambda: start_run(run, self.staff), lambda: cancel_run(run, self.staff),
                     lambda: set_stops(run, [order])):
            with self.assertRaises(RunError):
                step()
        deliver(run.stops.get(), self.staff, received_by='Fake Chef')
        with self.assertRaises(RunError):
            deliver(run.stops.get(), self.staff, received_by='Again')  # delivered once
        complete_run(run, self.staff)
        for step in (lambda: complete_run(run, self.staff), lambda: start_run(run, self.staff)):
            with self.assertRaises(RunError):
                step()

    def test_cancel_a_planned_run_frees_its_orders(self):
        order = self.picked_order()
        run = self.plan(order)
        cancel_run(run, self.staff)
        run.refresh_from_db()
        self.assertEqual(run.status, 'cancelled')
        self.assertEqual(self.status(order), 'picked')
        self.plan(order)  # it can go on another run

    def test_one_active_run_per_order(self):
        order = self.picked_order()
        run = self.plan(order)
        with self.assertRaises(RunError):
            self.plan(order)
        set_stops(run, [])  # taken off the planned run...
        self.plan(order)  # ...it can be planned elsewhere

    def test_only_picked_orders_of_the_company(self):
        confirmed = SalesOrder.objects.create(company=self.company, number=99, customer=self.customer,
                                              delivery_date=self.today, line1='x', town='y', postcode='TT1 1TT',
                                              status='confirmed')
        other = CompanyFactory()
        theirs = SalesOrder.objects.create(company=other, number=1, customer=TradeCustomer.objects.create(
            company=other, account_code='T-1', legal_name='E2E Fake Theirs'), delivery_date=self.today, line1='x',
            town='y', postcode='TT1 1TT', status='picked')
        for order in (confirmed, theirs):
            with self.subTest(order=order.pk), self.assertRaises(RunError):
                self.plan(order)

    def test_vehicle_and_driver_rules(self):
        order = self.picked_order()
        shop = Location.objects.create(company=self.company, code='SHOP-9', name='Fake shop', type='shop_floor')
        their_van = Location.objects.create(company=CompanyFactory(), code='VAN-T', name='Theirs', type='van')
        stock_operator, _ = get_user_model().objects.create_operator(self.company, 'Fake Stock', 'stock')
        their_driver, _ = get_user_model().objects.create_operator(CompanyFactory(), 'Fake Other', DRIVER)
        for vehicle, driver in ((shop, self.driver), (their_van, self.driver), (self.van, stock_operator),
                                (self.van, their_driver), (self.van, self.staff)):
            with self.subTest(vehicle=vehicle.code, driver=driver.pk), self.assertRaises(RunError):
                create_run(self.company, self.staff, run_date=self.today, vehicle=vehicle, driver=driver,
                           orders=[order])

    def test_start_is_refused_when_dispatch_lost_the_stock(self):
        order = self.picked_order()
        run = self.plan(order)
        lot = StockBatch.objects.get(location=dispatch_location(self.company), product=self.rice)
        with transaction.atomic():
            move(StockBatch.objects.select_for_update().get(pk=lot.pk), 2, 'outbound',
                 counterpart=virtual_location(self.company, 'waste'))
        with self.assertRaises(RunError):
            start_run(run, self.staff)
        run.refresh_from_db()
        self.assertEqual((run.status, self.status(order)), ('planned', 'picked'))
        self.assertEqual(self.held(self.van, self.chops), (0, D('0')))  # nothing moved


class ProofOfDeliveryTests(Base):
    def test_failed_stop_comes_back_to_dispatch_and_the_order_to_picked(self):
        delivered, failed = self.picked_order(), self.picked_order(rice=2, chops=1, chops_kg=D('0.500'))
        run = self.plan(delivered, failed)
        start_run(run, self.staff)
        deliver(run.stops.get(order=delivered), self.driver, received_by='Fake Chef')
        with self.assertRaises(RunError):
            fail_stop(run.stops.get(order=failed), self.driver, '   ')  # a reason is needed
        fail_stop(run.stops.get(order=failed), self.driver, 'Closed')
        self.assertEqual(self.status(failed), 'dispatched')  # still on the van until the run is back
        complete_run(run, self.staff)
        dispatch = dispatch_location(self.company)
        self.assertEqual(self.status(failed), 'picked')
        self.assertEqual(self.held(dispatch, self.rice), (2, D('0')))
        self.assertEqual(self.held(dispatch, self.chops), (1, D('0.500')))
        self.assertEqual(self.held(self.van, self.chops), (0, D('0')))
        again = self.plan(failed)  # back on a new run
        start_run(again, self.staff)
        deliver(again.stops.get(), self.staff, received_by='Fake Chef')
        self.assert_balances()

    def test_delivered_order_with_an_issued_invoice_becomes_invoiced(self):
        order = self.picked_order()
        with mock.patch.object(invoices.send_invoice_email, 'apply_async'):
            issue(create_invoice(order, self.staff), self.staff)
        run = self.plan(order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        self.assertEqual(self.status(order), 'invoiced')

    def test_only_staff_or_the_runs_driver_record_it(self):
        order = self.picked_order()
        run = self.plan(order)
        start_run(run, self.staff)
        other_driver, _ = get_user_model().objects.create_operator(self.company, 'Fake Driver Two', DRIVER)
        member = UserFactory(company=self.company)
        for user in (other_driver, member):
            with self.subTest(user=user.pk), self.assertRaises(RunError):
                deliver(run.stops.get(), user, received_by='x')
        with self.assertRaises(RunError):
            deliver(run.stops.get(), self.driver, received_by='  ')  # a name is needed
        stamped = timezone.now() - timedelta(minutes=5)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef', delivered_at=stamped)
        self.assertEqual(DeliveryStop.objects.get().delivered_at, stamped)

    def test_numbers_are_per_company(self):
        self.assertEqual([self.plan(self.picked_order()).number, self.plan(self.picked_order()).number], [1, 2])
        self.assertEqual(DeliveryRun.objects.filter(company=self.company).count(), 2)
