"""G59: sales and margin by channel (till, trade, online), waste, and the seeded week checked against the ledger."""

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db.models import Sum

from common.tests.factories import CompanyFactory
from inventory.models import Location, Product, StockMovement
from inventory.write_offs import raise_write_off
from reports.dashboard import dashboard, sales, waste
from reports.tests.helpers import MONDAY, LedgerCase, at, clock, replay_value
from sales.models import Sale
from sales.services import open_session, record_sale, void_sale
from sync.models import InboundEvent, StorefrontConnection
from tenants.models import Company, Till
from wholesale import invoices
from wholesale.models import (
    CreditNote, CreditNoteLine, CustomerReturn, SalesOrder, SalesOrderLine, SalesOrderPick, TradeCustomer,
)

D = Decimal
SUNDAY = MONDAY + timedelta(days=6)


def row(rows, **match):
    found = [r for r in rows if all(r[k] == v for k, v in match.items())]
    assert len(found) == 1, (match, rows)
    return found[0]


class SeededWeek(LedgerCase):
    """Two branches' tills, a void, an uncosted lot, write-offs; last week and this week."""

    def setUp(self):
        super().setUp()
        self.rice = self.product('Fake Rice')
        self.mince = self.product('Fake Mince', sold_by='weight', price_per_kg=D('8.9900'))
        week_before = MONDAY - timedelta(days=7)
        self.rice_n = self.receive(self.rice, self.north_shop, 20, cost='1.5000', when=at(week_before, 7))
        self.mince_n = self.receive(self.mince, self.north_shop, 0, cost='6.0000', when=at(week_before, 7), kg='10.000')
        self.rice_s = self.receive(self.rice, self.south_shop, 5, cost=None, when=at(week_before, 7))
        Product.objects.filter(pk=self.rice.pk).update(stock_quantity=25)
        self.north_till = Till.objects.create(branch=self.north, number=1, device_id='fake-n1')
        self.south_till = Till.objects.create(branch=self.south, number=1, device_id='fake-s1')
        with clock(at(week_before, 8)):
            self.north_session = open_session(till=self.north_till, user=self.staff, opening_float=D('0'))
            self.south_session = open_session(till=self.south_till, user=self.staff, opening_float=D('0'))
        self.sell(self.north_session, [(self.rice, 1)], at(week_before + timedelta(days=1)))  # last week: 2.00
        self.sell(self.north_session, [(self.rice, 3), (self.mince, 1, D('0.500'))], at(MONDAY + timedelta(days=1)))
        voided = self.sell(self.north_session, [(self.rice, 2)], at(MONDAY + timedelta(days=1), 13))
        with clock(at(MONDAY + timedelta(days=1), 14)):
            void_sale(sale=voided, user=self.staff, reason='Fake mistake')
        self.sell(self.south_session, [(self.rice, 2)], at(MONDAY + timedelta(days=2)))  # uncosted lot
        with clock(at(MONDAY + timedelta(days=3))):
            raise_write_off(company=self.company, user=self.staff, batch=self.rice_n, reason='damaged', quantity=1)
            raise_write_off(company=self.company, user=self.staff, batch=self.mince_n, reason='trim',
                            weight_kg=D('0.250'))

    def sell(self, session, lines, when):
        with clock(when):
            sale, _ = record_sale(session=session, cashier=self.staff, payment_method=Sale.CARD,
                                  amount_tendered=None, lines=lines, card_auth_code='AUTH01')
        return sale


class TillSalesTests(SeededWeek):
    def test_till_sales_and_margin_by_branch(self):
        rows = sales(self.company, MONDAY, SUNDAY)
        north = row(rows, channel='till', branch=self.north.pk)
        # 3 rice × 2.00 + 0.5 kg × 8.99 (4.495 -> 4.50); cost 3 × 1.50 + 0.5 kg × 6.00; the voided sale left out.
        self.assertEqual((north['net'], north['costed_net'], north['cost'], north['margin'], north['margin_percent'],
                          north['uncosted_net'], north['units'], north['kg']),
                         (D('10.50'), D('10.50'), D('7.50'), D('3.00'), D('28.57'), D('0.00'), 3, D('0.500')))
        south = row(rows, channel='till', branch=self.south.pk)
        self.assertEqual((south['net'], south['costed_net'], south['uncosted_net'], south['margin_percent']),
                         (D('4.00'), D('0.00'), D('4.00'), None))
        last = row(sales(self.company, MONDAY - timedelta(days=7), MONDAY - timedelta(days=1)), channel='till')
        self.assertEqual((last['net'], last['cost'], last['margin_percent']), (D('2.00'), D('1.50'), D('25.00')))

    def test_waste_by_location(self):
        [north] = waste(self.company, MONDAY, SUNDAY)
        self.assertEqual((north['location_code'], north['count'], north['units'], north['kg'], north['cost'],
                          north['uncosted']), (self.north_shop.code, 2, 1, D('0.250'), D('3.00'), 0))
        self.assertEqual(waste(self.company, MONDAY - timedelta(days=7), MONDAY - timedelta(days=1)), [])

    def test_the_seeded_week_matches_the_ledger(self):
        start, end = at(MONDAY, 0), at(SUNDAY + timedelta(days=1), 0)
        this_week = StockMovement.objects.filter(batch__product__company=self.company, date_time__gte=start,
                                                 date_time__lt=end)

        def net_to(kind, branch):
            moves = this_week.filter(batch__location__branch=branch)
            out = moves.filter(movement_type='outbound', destination_location__type=kind)
            back = moves.filter(movement_type='inbound', source_location__type=kind)
            units = (out.aggregate(n=Sum('quantity'))['n'] or 0) - (back.aggregate(n=Sum('quantity'))['n'] or 0)
            kg = (out.aggregate(n=Sum('weight_kg'))['n'] or 0) - (back.aggregate(n=Sum('weight_kg'))['n'] or 0)
            return units, D(kg)

        report = dashboard(self.company, MONDAY, today=SUNDAY)
        for branch in (self.north, self.south):
            till = row(report['current']['sales'], channel='till', branch=branch.pk)
            self.assertEqual((till['units'], till['kg']), net_to(Location.CUSTOMER, branch), branch.name)
        [wasted] = report['current']['waste']
        self.assertEqual((wasted['units'], wasted['kg']), net_to(Location.WASTE, self.north))
        for period, until in (('current', end), ('previous', start)):
            stock = {r['location_code']: r['value'] for r in report[period]['stock']}
            self.assertEqual(stock, replay_value(self.company, until), period)
        self.assertEqual(report['current']['totals']['net'], D('14.50'))
        self.assertEqual(report['previous']['totals']['net'], D('2.00'))
        self.assertEqual((report['week_start'], report['days']), (MONDAY, 7))

    def test_another_company_sees_nothing(self):
        report = dashboard(CompanyFactory(), MONDAY, today=SUNDAY)
        self.assertEqual((report['current']['sales'], report['current']['waste'], report['current']['stock']),
                         ([], [], []))


class TradeAndOnlineTests(LedgerCase):
    def setUp(self):
        super().setUp()
        Company.objects.filter(pk=self.company.pk).update(vat_number='GB123456789')
        self.company.refresh_from_db()
        self.rice = self.product('Fake Rice', external_id='m18-rice')
        self.cola = self.product('Fake Cola', vat_code='standard', external_id='m18-cola')
        day = MONDAY - timedelta(days=7)
        self.cheap = self.receive(self.rice, self.warehouse, 50, cost='1.4000', when=at(day))
        self.dear = self.receive(self.rice, self.warehouse, 50, cost='1.6000', when=at(day))
        self.free = self.receive(self.cola, self.warehouse, 50, cost=None, when=at(day))

    def test_trade_invoices_less_credit_notes_at_the_warehouse(self):
        customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                legal_name='E2E Fake Bistro Ltd', invoice_email='a@example.test')
        order = SalesOrder.objects.create(company=self.company, number=1, customer=customer,
                                          delivery_date=MONDAY, line1='1 Fake St', town='Testtown', postcode='TT1 1TT',
                                          status=SalesOrder.PICKED, source_location=self.warehouse,
                                          created_by=self.staff)
        rice = SalesOrderLine.objects.create(order=order, product=self.rice, quantity=10, unit_price=D('1.8000'),
                                             price_source='base', vat_code='zero', vat_rate=D('0'), picked_quantity=10)
        cola = SalesOrderLine.objects.create(order=order, product=self.cola, quantity=1, unit_price=D('1.0000'),
                                             price_source='base', vat_code='standard', vat_rate=D('20'),
                                             picked_quantity=1)
        SalesOrderPick.objects.create(line=rice, batch=self.cheap, quantity=6)
        SalesOrderPick.objects.create(line=rice, batch=self.dear, quantity=4)  # average 1.48
        SalesOrderPick.objects.create(line=cola, batch=self.free, quantity=1)
        with mock.patch('wholesale.invoices.uk_date', return_value=MONDAY + timedelta(days=1)), \
                mock.patch.object(invoices.send_invoice_email, 'apply_async'):
            invoice = invoices.issue(invoices.create_invoice(order, self.staff), self.staff)
        returned = CustomerReturn.objects.create(company=self.company, number=1, order=order, kind='later',
                                                 status='received', created_by=self.staff)
        note = CreditNote.objects.create(company=self.company, customer_return=returned, invoice=invoice)
        CreditNoteLine.objects.create(credit_note=note, invoice_line=invoice.lines.get(product=self.rice),
                                      description='Fake Rice', quantity=2, unit_price=D('1.8000'), vat_code='zero',
                                      vat_rate=D('0'), net=D('3.60'), vat=D('0'), gross=D('3.60'))
        note.status, note.number, note.credit_date = CreditNote.ISSUED, 1, MONDAY + timedelta(days=3)
        note.save()  # issued after its lines, as credit_notes.issue_credit_note does
        trade = row(sales(self.company, MONDAY, SUNDAY), channel='trade')
        # 18.00 + 1.00 invoiced − 3.60 credited; cost (10 − 2) × 1.48; cola picked from an uncosted lot.
        self.assertEqual((trade['location_code'], trade['branch_name'], trade['net'], trade['costed_net'],
                          trade['cost'], trade['margin'], trade['uncosted_net'], trade['units']),
                         ('WH-FAKE', 'No branch', D('15.40'), D('14.40'), D('11.84'), D('2.56'), D('1.00'), 9))
        self.assertEqual(sales(self.company, MONDAY - timedelta(days=7), MONDAY - timedelta(days=1)), [])

    def test_online_orders_net_of_vat_and_uncosted(self):
        connection = StorefrontConnection.objects.create(company=self.company, webhook_secret='fake-secret-x')
        other = StorefrontConnection.objects.create(company=CompanyFactory(), webhook_secret='fake-secret-y')

        def event(conn, status, items, when):
            with clock(when):
                InboundEvent.objects.create(connection=conn, event_id=uuid.uuid4(),
                                            event_type='order.paid', status=status,
                                            payload={'data': {'order_id': 'fake', 'items': items}})

        items = [{'product_id': 'm18-rice', 'quantity': 2, 'unit_price': '2.40'},
                 {'product_id': 'm18-cola', 'quantity': 1, 'unit_price': '1.20'},
                 {'product_id': 'm18-unknown', 'quantity': 1, 'unit_price': '9.99'}]
        event(connection, 'applied', items, at(MONDAY + timedelta(days=1)))
        event(connection, 'unmatched', items, at(MONDAY + timedelta(days=1)))  # nothing taken: not a sale here
        event(other, 'applied', items, at(MONDAY + timedelta(days=1)))
        online = row(sales(self.company, MONDAY, SUNDAY), channel='online')
        # 4.80 zero-rated + 1.20 with 20% VAT inside (0.20) = 5.80 net; nothing costed.
        self.assertEqual((online['net'], online['costed_net'], online['uncosted_net'], online['units'],
                          online['margin_percent']), (D('5.80'), D('0.00'), D('5.80'), 3, None))

    def test_a_week_still_running_compares_the_same_days(self):
        report = dashboard(self.company, MONDAY + timedelta(days=4), today=MONDAY + timedelta(days=2))
        self.assertEqual((report['week_start'], report['end'], report['previous_start'], report['previous_end'],
                          report['days']),
                         (MONDAY, MONDAY + timedelta(days=2), MONDAY - timedelta(days=7),
                          MONDAY - timedelta(days=5), 3))

