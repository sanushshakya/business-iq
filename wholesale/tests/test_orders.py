"""G40: sales orders — the status machine and the price snapshot taken at confirmation. All names are fake."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from inventory.models import Location, Product
from vat.models import VatRate
from wholesale.models import PriceList, PriceListEntry, SalesOrder, SalesOrderLine, TradeCustomer
from wholesale.orders import (
    CANCELLABLE,
    TRANSITIONS,
    OrderStateError,
    advance,
    cancel,
    confirm,
    next_order_number,
)

D = Decimal
DELIVERY = date(2026, 11, 13)  # a Friday


class Base(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.warehouse = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse',
                                                 type=Location.WAREHOUSE)  # G41: orders are picked from here
        self.customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                     legal_name='E2E Fake Bistro Ltd', delivery_days=['mon', 'fri'])
        self.each = ProductFactory(company=self.company, wholesale_price=D('1.0050'), vat_code='standard')
        self.loose = ProductFactory(company=self.company, sold_by=Product.WEIGHT, price_per_kg=D('12.99'),
                                    wholesale_price=D('8.0000'))
        self.catch = ProductFactory(company=self.company, sold_by=Product.WEIGHT, is_catch_weight=True,
                                    price_per_kg=D('9.99'), nominal_weight_kg=D('1.850'),
                                    wholesale_price=D('6.0000'))

    def order(self, lines=None, **fields):
        fields = {'company': self.company, 'customer': self.customer, 'delivery_date': DELIVERY,
                  'number': next_order_number(self.company), 'created_by': self.staff, 'line1': '1 Fake Street',
                  'town': 'Testtown', 'postcode': 'TT1 1TT', 'source_location': self.warehouse, **fields}
        order = SalesOrder.objects.create(**fields)
        for product, quantity, weight in (lines if lines is not None else [(self.each, 3, None)]):
            SalesOrderLine.objects.create(order=order, product=product, quantity=quantity, weight_kg=weight)
        return order


class StatusMachineTests(Base):
    def test_every_pair(self):
        statuses = [value for value, _label in SalesOrder.STATUSES]
        for start in statuses:
            for to in statuses:
                with self.subTest(start=start, to=to):
                    order = self.order(status=start)
                    allowed = to in TRANSITIONS[start]
                    try:
                        if to == SalesOrder.CONFIRMED:
                            confirm(order, self.staff)
                        elif to == SalesOrder.CANCELLED:
                            cancel(order, self.staff, 'Fake reason')
                        else:
                            advance(order, to)
                    except OrderStateError:
                        self.assertFalse(allowed, f'{start} -> {to} should be allowed')
                        order.refresh_from_db()
                        self.assertEqual(order.status, start)
                    else:
                        self.assertTrue(allowed, f'{start} -> {to} should be refused')
                        order.refresh_from_db()
                        self.assertEqual(order.status, to)

    def test_the_machine(self):
        self.assertEqual(TRANSITIONS, {
            'draft': {'confirmed', 'cancelled'},
            'confirmed': {'picking', 'cancelled'},
            'picking': {'picked', 'cancelled'},
            'picked': {'dispatched'},
            'dispatched': {'delivered', 'picked'},
            'delivered': {'invoiced'},
            'invoiced': set(),
            'cancelled': set(),
        })
        self.assertEqual(CANCELLABLE, {'draft', 'confirmed', 'picking'})

    def test_advance_does_not_confirm_or_cancel(self):
        for to in ('confirmed', 'cancelled'):
            with self.subTest(to=to), self.assertRaises(OrderStateError):
                advance(self.order(), to)

    def test_cancel_needs_a_reason_and_records_it(self):
        order = self.order(status='confirmed')
        for reason in ('', '   ', None):
            with self.subTest(reason=reason), self.assertRaises(OrderStateError):
                cancel(order, self.staff, reason)
        cancel(order, self.staff, '  Customer rang to cancel  ')
        order.refresh_from_db()
        self.assertEqual((order.status, order.cancel_reason, order.cancelled_by), (
            'cancelled', 'Customer rang to cancel', self.staff))
        self.assertIsNotNone(order.cancelled_at)

    def test_numbers_are_per_company(self):
        self.assertEqual([self.order().number, self.order().number], [1, 2])
        other = CompanyFactory()
        self.assertEqual(next_order_number(other), 1)
        self.assertEqual(self.order().number_display, 'SO-000003')


class ConfirmTests(Base):
    def test_snapshot_and_totals(self):
        price_list = PriceList.objects.create(company=self.company, name='Fake List')
        price_list.customers.add(self.customer)
        # Catch-weight: 2 pieces x 1.850 = 3.700 kg estimated, which reaches the 3.5 kg break.
        PriceListEntry.objects.create(price_list=price_list, product=self.catch, min_quantity=D('3.5'),
                                      unit_price=D('5.5000'))
        order = self.order([(self.each, 3, None), (self.loose, None, D('2.345')), (self.catch, 2, None)])
        confirm(order, self.staff)
        order.refresh_from_db()
        each, loose, catch = order.lines.order_by('id')
        # 3 x 1.0050 = 3.015 -> 3.02 (half up); VAT 20% of 3.02 = 0.604 -> 0.60.
        self.assertEqual((each.unit_price, each.price_source, each.vat_code, each.vat_rate),
                         (D('1.0050'), 'base', 'standard', D('20.00')))
        self.assertEqual((each.estimated_net, each.estimated_vat), (D('3.02'), D('0.60')))
        # 2.345 kg x 8.0000 = 18.76; zero-rated.
        self.assertEqual((loose.estimated_net, loose.estimated_vat, loose.vat_rate), (D('18.76'), D('0.00'), D('0.00')))
        self.assertEqual((catch.weight_kg, catch.unit_price, catch.price_source, catch.price_list_name,
                          catch.min_quantity), (D('3.700'), D('5.5000'), 'customer_list', 'Fake List', D('3.500')))
        self.assertEqual(catch.estimated_net, D('20.35'))
        self.assertEqual((order.status, order.confirmed_by), ('confirmed', self.staff))
        self.assertIsNotNone(order.confirmed_at)
        self.assertEqual((order.estimated_net, order.estimated_vat, order.estimated_gross),
                         (D('42.13'), D('0.60'), D('42.73')))

    def test_prices_on_the_delivery_date(self):
        ended = PriceList.objects.create(company=self.company, name='November', valid_to=date(2026, 11, 30))
        ended.customers.add(self.customer)
        PriceListEntry.objects.create(price_list=ended, product=self.each, unit_price=D('0.9000'))
        november = self.order()
        december = self.order(delivery_date=date(2026, 12, 4))
        confirm(november, self.staff)
        confirm(december, self.staff)
        self.assertEqual(november.lines.get().unit_price, D('0.9000'))
        self.assertEqual(december.lines.get().unit_price, D('1.0050'))

    def test_vat_rate_on_the_delivery_date(self):
        VatRate.objects.create(code='standard', rate=D('22.50'), valid_from=date(2026, 12, 1))
        order = self.order(delivery_date=date(2026, 12, 4))
        confirm(order, self.staff)
        self.assertEqual(order.lines.get().vat_rate, D('22.50'))

    def test_snapshot_does_not_change_after_confirmation(self):
        price_list = PriceList.objects.create(company=self.company, name='Fake List')
        price_list.customers.add(self.customer)
        entry = PriceListEntry.objects.create(price_list=price_list, product=self.each, unit_price=D('0.9500'))
        order = self.order([(self.each, 10, None), (self.loose, None, D('1.000'))])
        confirm(order, self.staff)
        before = list(order.lines.order_by('id').values())
        totals = SalesOrder.objects.filter(pk=order.pk).values('estimated_net', 'estimated_vat', 'estimated_gross')[0]

        entry.unit_price = D('2.0000')
        entry.save()
        price_list.is_active = False
        price_list.save()
        Product.objects.filter(pk__in=[self.each.pk, self.loose.pk]).update(wholesale_price=D('99.0000'),
                                                                             vat_code='reduced')
        VatRate.objects.create(code='standard', rate=D('30.00'), valid_from=date(2026, 11, 1))
        with self.assertRaises(OrderStateError):
            confirm(order, self.staff)  # a second confirm is refused, not a re-price

        self.assertEqual(list(order.lines.order_by('id').values()), before)
        self.assertEqual(
            SalesOrder.objects.filter(pk=order.pk).values('estimated_net', 'estimated_vat', 'estimated_gross')[0],
            totals)
        self.assertEqual(before[0]['unit_price'], D('0.9500'))

    def test_refused_with_no_price(self):
        Product.objects.filter(pk=self.loose.pk).update(wholesale_price=None)
        order = self.order([(self.each, 1, None), (self.loose, None, D('1.000'))])
        with self.assertRaises(OrderStateError) as caught:
            confirm(order, self.staff)
        self.assertIn(self.loose.name, str(caught.exception))
        order.refresh_from_db()
        self.assertEqual(order.status, 'draft')
        self.assertIsNone(order.lines.get(product=self.each).unit_price)  # nothing half-written

    def test_refused_for_a_customer_on_hold_or_closed(self):
        for status in ('on_hold', 'closed'):
            with self.subTest(status=status):
                TradeCustomer.objects.filter(pk=self.customer.pk).update(status=status)
                order = self.order()
                with self.assertRaises(OrderStateError):
                    confirm(order, self.staff)
                cancel(order, self.staff, 'On hold')  # cancelling still works

    def test_refused_for_catch_weight_without_a_nominal_weight(self):
        Product.objects.filter(pk=self.catch.pk).update(nominal_weight_kg=None)
        with self.assertRaises(OrderStateError):
            confirm(self.order([(self.catch, 2, None)]), self.staff)

    def test_refused_with_no_lines(self):
        with self.assertRaises(OrderStateError):
            confirm(self.order([]), self.staff)

    def test_off_delivery_day(self):
        self.assertFalse(self.order().off_delivery_day)  # Friday
        self.assertTrue(self.order(delivery_date=date(2026, 11, 11)).off_delivery_day)  # Wednesday
        TradeCustomer.objects.filter(pk=self.customer.pk).update(delivery_days=[])
        self.assertFalse(self.order(customer=TradeCustomer.objects.get(pk=self.customer.pk)).off_delivery_day)
