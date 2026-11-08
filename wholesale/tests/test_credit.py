"""G48: credit limits, overdue invoices (oldest first) and the manual hold block confirm, start-pick and the pick
list; a staff override with a reason lets one order through and is logged."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import transaction

from common.models import Setting
from wholesale import credit
from wholesale.credit import CreditBlocked
from wholesale.invoices import create_invoice, issue
from wholesale.ledger import post_adjustment
from wholesale.models import CreditOverride, SalesOrder, SalesOrderLine, TradeCustomer
from wholesale.orders import confirm, next_order_number
from wholesale.picking import start_pick
from wholesale.tests.test_credit_notes import CreditBase

D = Decimal


class CreditCase(CreditBase):
    def setUp(self):
        super().setUp()
        TradeCustomer.objects.filter(pk=self.customer.pk).update(payment_terms_days=0)  # due on the invoice day
        self.customer.refresh_from_db()

    def draft(self, rice=1):
        """A draft of ``rice`` units at £2.00 (zero-rated): estimated gross 2.00 × rice."""
        with transaction.atomic():
            order = SalesOrder.objects.create(
                company=self.company, number=next_order_number(self.company), customer=self.customer,
                delivery_date=self.today, line1='1 Fake Street', town='Testtown', postcode='TT1 1TT',
                created_by=self.staff, source_location=self.warehouse)
        SalesOrderLine.objects.create(order=order, product=self.rice, quantity=rice)
        return order

    def limit(self, amount):
        TradeCustomer.objects.filter(pk=self.customer.pk).update(credit_limit=amount)

    def blocked(self, call, *codes):
        with self.assertRaises(CreditBlocked) as caught:
            call()
        self.assertEqual([reason['code'] for reason in caught.exception.reasons], list(codes))
        return caught.exception

    def later(self, days):
        return mock.patch.object(credit, 'uk_date', return_value=self.today + timedelta(days=days))



class CreditTests(CreditCase):
    def test_no_limit_never_blocks(self):
        post_adjustment(self.customer, D('99999.00'), 'Fake opening balance', self.staff)
        self.assertEqual(confirm(self.draft(rice=5), self.staff).status, 'confirmed')

    def test_limit_counts_the_balance_and_open_orders_and_exactly_at_it_passes(self):
        self.limit(D('10.00'))
        post_adjustment(self.customer, D('4.00'), 'Fake opening balance', self.staff)
        confirm(self.draft(rice=1), self.staff)  # 4.00 + 2.00
        confirm(self.draft(rice=2), self.staff)  # 4.00 + 2.00 + 4.00 = 10.00: at the limit
        order = self.draft(rice=1)
        error = self.blocked(lambda: confirm(order, self.staff), 'over_limit')
        self.assertEqual((error.reasons[0]['exposure'], error.reasons[0]['credit_limit']), ('12.00', '10.00'))
        order.refresh_from_db()
        self.assertEqual((order.status, order.estimated_gross), ('draft', None))  # nothing kept
        self.assertIsNone(order.lines.get().unit_price)

    def test_an_issued_invoice_is_counted_once(self):
        order = self.picked_order()  # estimate 12.00, invoiced 12.24
        issue(create_invoice(order, self.staff), self.staff)
        self.limit(D('14.24'))
        report = credit.status(self.customer)
        self.assertEqual((report['balance'], report['open_orders'], report['exposure']),
                         (D('12.24'), D('0.00'), D('12.24')))
        confirm(self.draft(rice=1), self.staff)  # 12.24 + 2.00 = 14.24

    def test_overdue_oldest_first_after_n_days(self):
        first = issue(create_invoice(self.picked_order(), self.staff), self.staff)  # 12.24, due today
        second = issue(create_invoice(self.picked_order(rice=1), self.staff), self.staff)  # 2.00 + 6.24 = 8.24
        post_adjustment(self.customer, D('-15.00'), 'Fake part payment', self.staff)  # pays the first, 2.76 of 2nd
        with self.later(7):  # due + 7 is not before today: not yet
            confirm(self.draft(), self.staff)
        order = self.draft()
        with self.later(8):
            error = self.blocked(lambda: confirm(order, self.staff), 'overdue')
        self.assertEqual(error.reasons[0]['invoices'], [
            {'invoice': second.pk, 'number': second.number_display, 'due_date': second.due_date.isoformat(),
             'open': '5.48'}])
        self.assertNotIn(first.number_display, error.detail)
        post_adjustment(self.customer, D('-5.48'), 'Fake rest', self.staff)
        with self.later(30):
            confirm(order, self.staff)

    def test_n_is_a_company_setting(self):
        issue(create_invoice(self.picked_order(), self.staff), self.staff)
        setting = Setting.objects.create(company=self.company, key='credit_overdue_days', value='0')
        order = self.draft()
        with self.later(1):
            self.blocked(lambda: confirm(order, self.staff), 'overdue')
        for bad in ('soon', '-3', '2.5'):
            Setting.objects.filter(pk=setting.pk).update(value=bad)
            with self.subTest(value=bad), self.later(7):
                self.assertEqual(credit.overdue_days(self.company.pk), 7)
                self.assertEqual(credit.status(self.customer)['overdue'], [])

    def test_start_pick_and_the_pick_list_are_checked_too(self):
        order = confirm(self.draft(rice=3), self.staff)
        self.limit(D('5.00'))
        self.blocked(lambda: start_pick(order), 'over_limit')
        self.blocked(lambda: credit.check(order), 'over_limit')
        order.refresh_from_db()
        self.assertEqual(order.status, 'confirmed')

    def test_the_manual_hold_blocks_picking_and_is_not_overridden(self):
        order = confirm(self.draft(), self.staff)
        TradeCustomer.objects.filter(pk=self.customer.pk).update(status='on_hold', hold_reason='Fake dispute')
        error = self.blocked(lambda: start_pick(order), 'on_hold')
        self.assertIn('Fake dispute', error.detail)
        with self.assertRaises(credit.CreditError):
            credit.override(order, self.staff, 'Fake')

    def test_override_lets_that_order_through_and_is_logged(self):
        self.limit(D('1.00'))
        order = self.draft(rice=2)
        member = type(self.staff).objects.create_user(email='member@example.test', password=None,
                                                      company=self.company)
        with self.assertRaises(credit.CreditError):
            confirm(order, member, override_reason='Fake')
        with self.assertRaises(credit.CreditError):
            confirm(order, self.staff, override_reason='   ')
        confirm(order, self.staff, override_reason='Fake: owner agreed')
        logged = CreditOverride.objects.get(order=order)
        self.assertEqual((logged.created_by, logged.reason, logged.blocks, logged.exposure, logged.credit_limit),
                         (self.staff, 'Fake: owner agreed', ['over_limit'], D('4.00'), D('1.00')))
        start_pick(order)
        credit.check(order)
        self.blocked(lambda: confirm(self.draft(), self.staff), 'over_limit')  # another order is still blocked

    def test_an_override_needs_something_to_override(self):
        order = confirm(self.draft(), self.staff)
        with self.assertRaises(credit.CreditError):
            credit.override(order, self.staff, 'Fake')
        self.limit(D('1.00'))
        credit.override(order, self.staff, 'Fake: agreed')
        start_pick(order)
        self.assertEqual(CreditOverride.objects.filter(order=order).count(), 1)

    def test_confirming_without_needing_an_override_logs_none(self):
        confirm(self.draft(), self.staff, override_reason='Fake')
        self.assertFalse(CreditOverride.objects.exists())
