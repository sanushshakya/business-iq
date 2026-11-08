"""G45: goods refused at the door or returned later go back to a location or to waste, by reason."""

from decimal import Decimal

from django.db.models import Sum

from inventory.ledger import dispatch_location, virtual_location
from inventory.models import StockBatch, StockMovement
from wholesale.invoices import InvoiceError, create_invoice
from wholesale.models import CustomerReturn, ReturnLine, SalesOrderLine
from wholesale.returns import ReturnError, record_return
from wholesale.runs import RunError, complete_run, deliver, start_run
from wholesale.tests.test_runs import Base

D = Decimal


class DoorTests(Base):
    def out(self, order):
        run = self.plan(order)
        start_run(run, self.staff)
        return run

    def line(self, order, product):
        return SalesOrderLine.objects.get(order=order, product=product, substitute_for__isnull=True)

    def test_refused_goods_stay_on_the_van_then_go_by_reason(self):
        order = self.picked_order(rice=3, chops=2, chops_kg=D('1.040'))
        run = self.out(order)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef', refused=[
            {'line': self.line(order, self.rice).pk, 'quantity': 1, 'reason': 'not_wanted'},
            {'line': self.line(order, self.chops).pk, 'quantity': 1, 'weight_kg': D('0.500'), 'reason': 'damaged'},
        ])
        customer = virtual_location(self.company, 'customer')
        to_customer = StockMovement.objects.filter(destination_location=customer)
        self.assertEqual(to_customer.filter(batch__product=self.rice).aggregate(s=Sum('quantity'))['s'], 2)
        self.assertEqual(to_customer.filter(batch__product=self.chops).aggregate(s=Sum('weight_kg'))['s'],
                         D('0.540'))
        self.assertEqual(self.held(self.van, self.rice), (1, D('0')))  # still on the van
        returned = CustomerReturn.objects.get()
        self.assertEqual((returned.kind, returned.status, returned.number_display), ('door', 'on_van', 'RET-000001'))
        self.assertEqual(self.status(order), 'delivered')

        complete_run(run, self.staff)
        returned.refresh_from_db()
        self.assertEqual(returned.status, 'received')
        dispatch, waste = dispatch_location(self.company), virtual_location(self.company, 'waste')
        self.assertEqual(self.held(dispatch, self.rice), (1, D('0')))  # not wanted: back to dispatch
        self.assertEqual(StockMovement.objects.filter(destination_location=waste, batch__product=self.chops)
                         .aggregate(s=Sum('weight_kg'))['s'], D('0.500'))  # damaged: waste
        self.assertEqual(self.held(self.van, self.chops), (0, D('0.000')))
        self.assert_balances()

    def test_an_invoice_bills_only_what_was_delivered(self):
        order = self.picked_order(rice=3)
        draft = create_invoice(order, self.staff)  # drafted before the delivery
        run = self.out(order)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef', refused=[
            {'line': self.line(order, self.rice).pk, 'quantity': 2, 'reason': 'wrong_item'}])
        draft.refresh_from_db()
        self.assertEqual(draft.lines.get(product=self.rice).quantity, 1)  # rebuilt at the door

    def test_everything_refused(self):
        order = self.picked_order(rice=2, chops=1, chops_kg=D('0.500'))
        run = self.out(order)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef', refused=[
            {'line': self.line(order, self.rice).pk, 'quantity': 2, 'reason': 'not_wanted'},
            {'line': self.line(order, self.chops).pk, 'quantity': 1, 'weight_kg': D('0.500'),
             'reason': 'not_wanted'}])
        self.assertEqual(self.status(order), 'delivered')
        with self.assertRaises(InvoiceError):
            create_invoice(order, self.staff)  # nothing delivered, nothing to bill
        complete_run(run, self.staff)
        self.assert_balances()

    def test_refusal_limits(self):
        order = self.picked_order(rice=3, chops=2, chops_kg=D('1.040'))
        run = self.out(order)
        stop = run.stops.get()
        for refused in ([{'line': self.line(order, self.rice).pk, 'quantity': 4, 'reason': 'damaged'}],
                        [{'line': self.line(order, self.chops).pk, 'quantity': 1, 'reason': 'damaged'}],  # no kg
                        [{'line': self.line(order, self.chops).pk, 'quantity': 1, 'weight_kg': D('1.100'),
                          'reason': 'damaged'}],
                        [{'line': self.line(order, self.rice).pk, 'quantity': 1, 'reason': 'lost'}],
                        [{'line': 999999, 'quantity': 1, 'reason': 'damaged'}]):
            with self.subTest(refused=refused), self.assertRaises(RunError):
                deliver(stop, self.driver, received_by='Fake Chef', refused=refused)
        self.assertFalse(CustomerReturn.objects.exists())

    def test_completing_twice_moves_nothing_twice(self):
        order = self.picked_order(rice=3)
        run = self.out(order)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef', refused=[
            {'line': self.line(order, self.rice).pk, 'quantity': 1, 'reason': 'not_wanted'}])
        complete_run(run, self.staff)
        movements = StockMovement.objects.count()
        with self.assertRaises(RunError):
            complete_run(run, self.staff)
        self.assertEqual(StockMovement.objects.count(), movements)


class LaterReturnTests(Base):
    def delivered(self, **amounts):
        order = self.picked_order(**amounts)
        run = self.plan(order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        complete_run(run, self.staff)
        return order

    def line(self, order, product):
        return SalesOrderLine.objects.get(order=order, product=product, substitute_for__isnull=True)

    def test_restock_and_waste(self):
        order = self.delivered(rice=3, chops=2, chops_kg=D('1.040'))
        returned = record_return(order, self.staff, [
            {'line': self.line(order, self.rice).pk, 'quantity': 2, 'reason': 'not_wanted'},
            {'line': self.line(order, self.chops).pk, 'quantity': 1, 'weight_kg': D('0.520'),
             'reason': 'out_of_date'},
        ], note='Fake note')
        self.assertEqual((returned.kind, returned.status), ('later', 'received'))
        self.assertEqual(self.held(self.warehouse, self.rice)[0], 20 - 3 + 2)  # back to the order's warehouse
        waste = virtual_location(self.company, 'waste')
        self.assertEqual(StockMovement.objects.filter(destination_location=waste, batch__product=self.chops)
                         .aggregate(s=Sum('weight_kg'))['s'], D('0.520'))
        lot = StockBatch.objects.get(location=self.warehouse, product=self.rice)
        self.assertEqual(lot.batch_number, 'RICE-1')  # the same lot it was picked from
        self.assert_balances()

    def test_restock_to_a_chosen_location(self):
        order = self.delivered(rice=3)
        dispatch = dispatch_location(self.company)
        record_return(order, self.staff, [{'line': self.line(order, self.rice).pk, 'quantity': 1,
                                           'reason': 'wrong_item', 'location': dispatch}])
        self.assertEqual(self.held(dispatch, self.rice), (1, D('0')))

    def test_never_more_than_delivered(self):
        order = self.picked_order(rice=3)
        run = self.plan(order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef', refused=[
            {'line': self.line(order, self.rice).pk, 'quantity': 1, 'reason': 'not_wanted'}])
        complete_run(run, self.staff)  # 2 delivered
        record_return(order, self.staff, [{'line': self.line(order, self.rice).pk, 'quantity': 1,
                                           'reason': 'damaged'}])
        with self.assertRaises(ReturnError):
            record_return(order, self.staff, [{'line': self.line(order, self.rice).pk, 'quantity': 2,
                                               'reason': 'damaged'}])
        record_return(order, self.staff, [{'line': self.line(order, self.rice).pk, 'quantity': 1,
                                           'reason': 'damaged'}])
        self.assertEqual(ReturnLine.objects.filter(customer_return__kind='later').aggregate(
            s=Sum('quantity'))['s'], 2)

    def test_only_delivered_or_invoiced_orders(self):
        order = self.picked_order()
        with self.assertRaises(ReturnError):
            record_return(order, self.staff, [{'line': self.line(order, self.rice).pk, 'quantity': 1,
                                               'reason': 'damaged'}])

    def test_catch_weight_pieces_and_kg_stay_consistent(self):
        order = self.delivered(rice=1, chops=2, chops_kg=D('1.040'))
        line = self.line(order, self.chops).pk
        for amounts in ({'quantity': 1}, {'quantity': 3, 'weight_kg': D('1.000')},
                        {'quantity': 1, 'weight_kg': D('1.041')}, {'weight_kg': D('0.5')}):
            with self.subTest(amounts=amounts), self.assertRaises(ReturnError):
                record_return(order, self.staff,
                              [{'line': line, 'reason': 'damaged', **amounts}])
        record_return(order, self.staff, [{'line': line, 'quantity': 2, 'weight_kg': D('1.040'),
                                           'reason': 'damaged'}])
        self.assert_balances()


class GuardrailFixTests(Base):
    def line(self, order, product):
        return SalesOrderLine.objects.get(order=order, product=product, substitute_for__isnull=True)

    def test_one_line_twice_in_a_request_is_refused_at_the_door_and_later(self):
        order = self.picked_order(rice=3)
        run = self.plan(order)
        start_run(run, self.staff)
        twice = [{'line': self.line(order, self.rice).pk, 'quantity': 3, 'reason': 'not_wanted'}] * 2
        with self.assertRaises(RunError):
            deliver(run.stops.get(), self.driver, received_by='Fake Chef', refused=twice)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        complete_run(run, self.staff)
        with self.assertRaises(ReturnError):
            record_return(order, self.staff, twice)
        self.assertEqual(self.held(self.warehouse, self.rice)[0], 17)  # nothing booked in

    def test_an_out_of_date_lot_is_never_restocked_to_a_sellable_place(self):
        from datetime import timedelta

        from inventory.models import Location

        order = self.picked_order(rice=3)
        run = self.plan(order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        complete_run(run, self.staff)
        StockBatch.objects.filter(batch_number='RICE-1').update(expiration_date=self.today - timedelta(days=1))
        shop = Location.objects.create(company=self.company, code='SHOP-9', name='Fake shop', type='shop_floor',
                                       sellable=True)
        with self.assertRaises(ReturnError):
            record_return(order, self.staff, [{'line': self.line(order, self.rice).pk, 'quantity': 1,
                                               'reason': 'not_wanted', 'location': shop}])

    def test_a_short_van_still_takes_door_refusals_off_it(self):
        from django.db import transaction

        from inventory.ledger import move

        order = self.picked_order(rice=3)
        run = self.plan(order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef', refused=[
            {'line': self.line(order, self.rice).pk, 'quantity': 2, 'reason': 'not_wanted'}])
        lot = StockBatch.objects.get(location=self.van, product=self.rice)
        with transaction.atomic():
            move(StockBatch.objects.select_for_update().get(pk=lot.pk), 1, 'outbound',
                 counterpart=virtual_location(self.company, 'waste'))
        complete_run(run, self.staff)
        self.assertEqual(self.held(dispatch_location(self.company), self.rice), (1, D('0')))
        self.assert_balances()
