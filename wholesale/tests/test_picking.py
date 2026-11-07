"""G42: picking a confirmed trade order — scans, real weights, substitutions, and finishing to DISPATCH."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.ledger import dispatch_location, move, virtual_location
from inventory.models import Location, Product, StockBatch, StockMovement, StockReservation
from wholesale.models import SalesOrder, SalesOrderLine, SalesOrderPick, TradeCustomer
from wholesale.orders import OrderStateError, cancel, confirm, next_order_number
from wholesale.picking import decide, finish_pick, propose_substitute, record_pick, remove_pick, start_pick

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.company = CompanyFactory()
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.other_staff = UserFactory(company=self.company, is_staff=True)
        self.warehouse = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse',
                                                 type=Location.WAREHOUSE)
        self.customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                     legal_name='E2E Fake Bistro Ltd')
        self.each = ProductFactory(company=self.company, stock_quantity=0, wholesale_price=D('2.0000'))
        self.loose = ProductFactory(company=self.company, stock_quantity=0, sold_by=Product.WEIGHT,
                                    price_per_kg=D('9.99'), wholesale_price=D('8.0000'), plu='42')
        self.catch = ProductFactory(company=self.company, stock_quantity=0, sold_by=Product.WEIGHT,
                                    is_catch_weight=True, price_per_kg=D('9.99'), nominal_weight_kg=D('1.500'),
                                    wholesale_price=D('6.0000'))

    def stock(self, product, quantity=0, kg=None, number=None, days=30, location=None):
        batch = StockBatchFactory(product=product, quantity=0, weight_kg=D('0'), location=location or self.warehouse,
                                  expiration_date=self.today + timedelta(days=days),
                                  **({'batch_number': number} if number else {}))
        with transaction.atomic():
            move(batch, quantity, 'inbound', counterpart=virtual_location(self.company, 'supplier'),
                 weight_kg=kg if product.is_weighed else None)
        batch.refresh_from_db()
        return batch

    def order(self, *lines):
        with transaction.atomic():
            order = SalesOrder.objects.create(
                company=self.company, number=next_order_number(self.company), customer=self.customer,
                delivery_date=self.today + timedelta(days=1), line1='1 Fake Street', town='Testtown',
                postcode='TT1 1TT', created_by=self.staff, source_location=self.warehouse)
        for product, quantity, kg in lines:
            SalesOrderLine.objects.create(order=order, product=product, quantity=quantity, weight_kg=kg)
        confirm(order, self.staff)
        start_pick(order)
        order.refresh_from_db()
        return order

    def line(self, order, product):
        return order.lines.get(product=product, substitute_for__isnull=True)

    def pick(self, order, product, **fields):
        return record_pick(order, self.line(order, product).pk, user=self.staff, **fields)


class DoneWhenTests(Base):
    def test_50_kg_ordered_51_2_kg_picked_and_the_ledger_balances(self):
        batch = self.stock(self.loose, kg=D('50.000'), number='LAMB-1')
        order = self.order((self.loose, None, D('50.000')))
        self.pick(order, self.loose, batch_number='LAMB-1', weight_kg=D('51.200'))
        finish_pick(order, self.staff)

        order.refresh_from_db()
        line = self.line(order, self.loose)
        self.assertEqual((order.status, line.picked_weight_kg), ('picked', D('51.200')))
        batch.refresh_from_db()
        dispatch = dispatch_location(self.company)
        at_dispatch = StockBatch.objects.get(location=dispatch, product=self.loose)
        self.assertEqual((batch.weight_kg, at_dispatch.weight_kg), (D('0.000'), D('51.200')))
        adjustment = virtual_location(self.company, 'adjustment')
        self.assertEqual(StockMovement.objects.get(source_location=adjustment).weight_kg, D('1.200'))
        for held in StockBatch.objects.filter(product=self.loose):
            moved_in = StockMovement.objects.filter(batch=held, movement_type='inbound').aggregate(
                s=Sum('weight_kg'))['s'] or 0
            moved_out = StockMovement.objects.filter(batch=held, movement_type='outbound').aggregate(
                s=Sum('weight_kg'))['s'] or 0
            self.assertEqual(moved_in - moved_out, held.weight_kg, held.location.code)
        # Supplier 50 + adjustment 1.2 = 51.2 at dispatch: nothing made up, nothing lost.
        self.assertEqual(StockBatch.objects.filter(product=self.loose).aggregate(s=Sum('weight_kg'))['s'],
                         D('51.200'))
        self.assertFalse(StockReservation.objects.filter(released_at__isnull=True).exists())


class RecordPickTests(Base):
    def setUp(self):
        super().setUp()
        self.each_batch = self.stock(self.each, 10, number='EACH-1')
        self.catch_batch = self.stock(self.catch, 4, kg=D('6.000'), number='CW-1')
        self.loose_batch = self.stock(self.loose, kg=D('10.000'), number='LOOSE-1')
        self.order_ = self.order((self.each, 5, None), (self.catch, 2, None), (self.loose, None, D('2.000')))

    def test_kinds_and_limits(self):
        order = self.order_
        self.pick(order, self.each, batch_number='EACH-1', quantity=3)
        with self.assertRaises(OrderStateError):
            self.pick(order, self.each, batch_number='EACH-1', quantity=3)  # 6 > 5 ordered
        self.pick(order, self.each, batch_number='EACH-1', quantity=2)
        self.pick(order, self.catch, batch_number='CW-1', quantity=2, weight_kg=D('3.400'))
        with self.assertRaises(OrderStateError):  # 3.000 kg estimated: over 120%
            self.pick(order, self.loose, batch_number='LOOSE-1', weight_kg=D('2.401'))
        self.pick(order, self.loose, batch_number='LOOSE-1', weight_kg=D('2.400'))
        bad = [
            (self.each, {'batch_number': 'EACH-1'}),  # no quantity
            (self.each, {'batch_number': 'EACH-1', 'quantity': 1, 'weight_kg': D('1')}),
            (self.loose, {'batch_number': 'LOOSE-1', 'quantity': 1}),
            (self.catch, {'batch_number': 'CW-1', 'quantity': 1}),  # no kg
            (self.loose, {'batch_number': 'NOPE', 'weight_kg': D('0.1')}),
            (self.loose, {'batch_number': 'EACH-1', 'weight_kg': D('0.1')}),  # another product's lot
            (self.loose, {'batch_number': 'LOOSE-1', 'weight_kg': D('0.0001')}),
        ]
        for product, fields in bad:
            with self.subTest(fields=fields), self.assertRaises(OrderStateError):
                self.pick(order, product, **fields)

    def test_a_lot_at_another_location_or_expired_is_refused(self):
        self.stock(self.each, 10, number='SHOP-LOT', location=Location.objects.create(
            company=self.company, code='SHOP-9', name='Fake shop', type='shop_floor'))
        self.stock(self.each, 10, number='OLD', days=-1)
        for number in ('SHOP-LOT', 'OLD'):
            with self.subTest(number=number), self.assertRaises(OrderStateError):
                self.pick(self.order_, self.each, batch_number=number, quantity=1)

    def test_another_orders_reservation_is_not_picked(self):
        # A second order holds the 5 remaining units of EACH-1.
        other = self.order((self.each, 5, None))
        with self.assertRaises(OrderStateError):
            self.pick(other, self.each, batch_number='EACH-1', quantity=6)
        self.pick(self.order_, self.each, batch_number='EACH-1', quantity=5)
        self.pick(other, self.each, batch_number='EACH-1', quantity=5)
        finish_pick(self.order_, self.staff)
        finish_pick(other, self.staff)
        self.each_batch.refresh_from_db()
        self.assertEqual(self.each_batch.quantity, 0)

    def test_scale_label_scan(self):
        from sales.tests.test_labels import add_rules, scale_code

        add_rules(self.company)
        code = scale_code('21', '0042', '01250', with_check=True)  # PLU 42, 1.250 kg
        pick = self.pick(self.order_, self.loose, barcode=code)
        self.assertEqual((pick.batch, pick.weight_kg, pick.barcode), (self.loose_batch, D('1.250'), code))
        with self.assertRaises(OrderStateError):  # a label for another product than the line's
            self.pick(self.order_, self.each, barcode=code)

    def test_remove_then_finish_moves_only_what_is_left(self):
        first = self.pick(self.order_, self.each, batch_number='EACH-1', quantity=3)
        self.pick(self.order_, self.each, batch_number='EACH-1', quantity=1)
        remove_pick(self.order_, first.pk)
        finish_pick(self.order_, self.staff)
        lines = {line.product_id: line for line in self.order_.lines.all()}
        self.assertEqual(lines[self.each.pk].picked_quantity, 1)
        self.assertEqual((lines[self.catch.pk].picked_quantity, lines[self.catch.pk].picked_weight_kg),
                         (0, D('0.000')))  # nothing picked: short
        self.assertEqual(lines[self.loose.pk].picked_weight_kg, D('0.000'))
        self.each_batch.refresh_from_db()
        self.assertEqual(self.each_batch.quantity, 9)

    def test_status_rules(self):
        with self.assertRaises(OrderStateError):
            finish_pick(self.order_, self.staff)  # nothing picked
        self.pick(self.order_, self.each, batch_number='EACH-1', quantity=1)
        finish_pick(self.order_, self.staff)
        for step in (lambda: finish_pick(self.order_, self.staff),
                     lambda: self.pick(self.order_, self.each, batch_number='EACH-1', quantity=1),
                     lambda: remove_pick(self.order_, SalesOrderPick.objects.get().pk)):
            with self.assertRaises(OrderStateError):
                step()
        draft = SalesOrder.objects.create(company=self.company, number=99, customer=self.customer,
                                          delivery_date=self.today, line1='x', town='y', postcode='TT1 1TT',
                                          source_location=self.warehouse)
        with self.assertRaises(OrderStateError):
            start_pick(draft)

    def test_cancel_while_picking_moves_nothing(self):
        self.pick(self.order_, self.each, batch_number='EACH-1', quantity=2)
        cancel(self.order_, self.staff, 'Fake reason')
        self.each_batch.refresh_from_db()
        self.assertEqual(self.each_batch.quantity, 10)
        self.assertFalse(StockReservation.objects.filter(released_at__isnull=True).exists())


class SubstitutionTests(Base):
    def setUp(self):
        super().setUp()
        self.thighs = ProductFactory(company=self.company, stock_quantity=0, sold_by=Product.WEIGHT,
                                     price_per_kg=D('7.99'), wholesale_price=D('5.2500'), vat_code='standard')
        self.stock(self.thighs, kg=D('20.000'), number='THIGH-1')
        self.stock(self.loose, kg=D('1.000'), number='BREAST-1')
        self.order_ = self.order((self.loose, None, D('5.000')))
        self.original = self.line(self.order_, self.loose)

    def propose(self, user=None, **amounts):
        return propose_substitute(self.order_, self.original.pk, self.thighs, user=user or self.staff,
                                  **(amounts or {'weight_kg': D('5.000')}))

    def test_propose_approve_price_and_pick(self):
        substitute = self.propose()
        self.assertEqual((substitute.substitution, substitute.substitute_for, substitute.unit_price),
                         ('proposed', self.original, None))
        with self.assertRaises(OrderStateError):
            record_pick(self.order_, substitute.pk, batch_number='THIGH-1', weight_kg=D('5'), user=self.staff)
        self.pick(self.order_, self.loose, batch_number='BREAST-1', weight_kg=D('1.000'))
        with self.assertRaises(OrderStateError):
            finish_pick(self.order_, self.staff)  # waiting for a decision
        with self.assertRaises(OrderStateError):
            decide(self.order_, substitute.pk, approve=True, user=self.staff)  # the proposer
        decide(self.order_, substitute.pk, approve=True, user=self.other_staff)
        substitute.refresh_from_db()
        self.assertEqual((substitute.substitution, substitute.decided_by), ('approved', self.other_staff))
        self.assertEqual((substitute.unit_price, substitute.price_source, substitute.vat_rate),
                         (D('5.2500'), 'base', D('20.00')))
        self.assertEqual((substitute.estimated_net, substitute.estimated_vat), (D('26.25'), D('5.25')))
        record_pick(self.order_, substitute.pk, batch_number='THIGH-1', weight_kg=D('4.950'), user=self.staff)
        finish_pick(self.order_, self.staff)
        substitute.refresh_from_db()
        self.assertEqual(substitute.picked_weight_kg, D('4.950'))

    def test_reject(self):
        substitute = self.propose()
        decide(self.order_, substitute.pk, approve=False, user=self.other_staff)
        substitute.refresh_from_db()
        self.assertEqual((substitute.substitution, substitute.unit_price), ('rejected', None))
        with self.assertRaises(OrderStateError):
            record_pick(self.order_, substitute.pk, batch_number='THIGH-1', weight_kg=D('5'), user=self.staff)
        with self.assertRaises(OrderStateError):
            decide(self.order_, substitute.pk, approve=True, user=self.other_staff)  # decided once
        self.pick(self.order_, self.loose, batch_number='BREAST-1', weight_kg=D('1.000'))
        finish_pick(self.order_, self.staff)  # a rejected swap doesn't block the pick
        substitute.refresh_from_db()
        self.assertIsNone(substitute.picked_weight_kg)

    def test_rules(self):
        non_staff = UserFactory(company=self.company)
        substitute = self.propose()
        with self.assertRaises(OrderStateError):
            decide(self.order_, substitute.pk, approve=True, user=non_staff)
        with self.assertRaises(OrderStateError):
            self.propose()  # one open proposal per line
        with self.assertRaises(OrderStateError):
            propose_substitute(self.order_, substitute.pk, self.each, quantity=1, user=self.staff)  # of a substitute
        for product, amounts in ((self.loose, {'weight_kg': D('1')}),  # the same product
                                 (ProductFactory(company=CompanyFactory()), {'quantity': 1}),
                                 (self.each, {'weight_kg': D('1')})):  # each product given kg
            with self.subTest(product=product.pk), self.assertRaises(OrderStateError):
                propose_substitute(self.order_, self.original.pk, product, user=self.staff, **amounts)

    def test_no_trade_price_refuses_the_approval(self):
        Product.objects.filter(pk=self.thighs.pk).update(wholesale_price=None)
        substitute = self.propose()
        with self.assertRaises(OrderStateError):
            decide(self.order_, substitute.pk, approve=True, user=self.other_staff)
        substitute.refresh_from_db()
        self.assertEqual(substitute.substitution, 'proposed')


class ReviewFixTests(Base):
    def test_a_catch_weight_swap_is_proposed_in_pieces(self):
        self.stock(self.each, 5, number='EACH-1')
        self.stock(self.catch, 4, kg=D('6.000'), number='CW-1')
        order = self.order((self.each, 5, None))
        swap = propose_substitute(order, self.line(order, self.each).pk, self.catch, quantity=2, user=self.staff)
        decide(order, swap.pk, approve=True, user=self.other_staff)
        swap.refresh_from_db()
        self.assertEqual((swap.quantity, swap.weight_kg, swap.unit_price), (2, D('3.000'), D('6.0000')))

    def test_picked_free_stock_is_held_until_finish(self):
        batch = self.stock(self.each, 5, number='EACH-1')
        first = self.order((self.each, 2, None))  # reserves 2 of the 5
        StockReservation.objects.filter(order_line__order=first).update(released_at=timezone.now())  # e.g. lost
        record_pick(first, self.line(first, self.each).pk, batch_number='EACH-1', quantity=2, user=self.staff)
        second = self.order((self.each, 5, None))  # may only reserve the 3 not in the picker's hands
        self.assertEqual(StockReservation.objects.filter(order_line__order=second, released_at__isnull=True)
                         .aggregate(s=Sum('quantity'))['s'], 3)
        finish_pick(first, self.staff)
        batch.refresh_from_db()
        self.assertEqual(batch.quantity, 3)

    def test_a_pick_from_another_lot_moves_the_lines_hold_there(self):
        self.stock(self.each, 3, number='A', days=10)
        self.stock(self.each, 3, number='B', days=20)
        order = self.order((self.each, 3, None))  # reserves A
        record_pick(order, self.line(order, self.each).pk, batch_number='B', quantity=3, user=self.staff)
        held = dict(StockReservation.objects.filter(order_line__order=order, released_at__isnull=True)
                    .values('batch__batch_number').annotate(s=Sum('quantity')).values_list('batch__batch_number', 's'))
        self.assertEqual(held, {'B': 3})  # A is free again for others

    def test_loose_pick_never_goes_over_free_kg_while_another_order_holds_the_lot(self):
        batch = self.stock(self.loose, kg=D('10.000'), number='LOOSE-1')
        self.order((self.loose, None, D('9.000')))  # holds 9 of the 10 kg
        order = self.order((self.loose, None, D('5.000')))  # only 1 kg left for it
        with self.assertRaises(OrderStateError):
            self.pick(order, self.loose, batch_number='LOOSE-1', weight_kg=D('1.001'))
        self.pick(order, self.loose, batch_number='LOOSE-1', weight_kg=D('1.000'))
        finish_pick(order, self.staff)
        batch.refresh_from_db()
        self.assertEqual(batch.weight_kg, D('9.000'))  # the other order's 9 kg is still there
        self.assertFalse(StockMovement.objects.filter(source_location__type='adjustment').exists())

    def test_ledger_books_no_kg_gap_into_a_lot_others_hold(self):
        from inventory.ledger import LedgerError, pick_batch

        batch = self.stock(self.loose, kg=D('10.000'), number='LOOSE-1')
        self.order((self.loose, None, D('9.000')))
        with transaction.atomic(), self.assertRaises(LedgerError):
            pick_batch(StockBatch.objects.select_for_update().select_related('location').get(pk=batch.pk),
                       quantity=0, weight_kg=D('1.500'), to=dispatch_location(self.company))


class PickAdminTests(TestCase):
    def test_picks_are_view_only(self):
        from django.contrib import admin

        model_admin = admin.site._registry[SalesOrderPick]
        request = type('Request', (), {'user': UserFactory(is_staff=True, is_superuser=True)})()
        for allowed in (model_admin.has_add_permission(request), model_admin.has_change_permission(request),
                        model_admin.has_delete_permission(request)):
            self.assertFalse(allowed)
