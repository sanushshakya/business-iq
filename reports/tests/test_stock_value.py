"""G59: stock value at an instant, rebuilt from the ledger, checked against an independent forward replay."""

from datetime import timedelta
from decimal import Decimal

from inventory.ledger import transfer_batch
from inventory.models import Location, StockBatch
from reports.dashboard import stock_value, week_bounds
from reports.tests.helpers import MONDAY, LedgerCase, at, clock, replay_value

D = Decimal


class StockValueTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.rice = self.product('Fake Rice')
        self.mince = self.product('Fake Mince', sold_by='weight', price_per_kg=D('8.9900'))
        day = MONDAY - timedelta(days=7)
        self.rice_n = self.receive(self.rice, self.north_shop, 20, cost='1.5000', when=at(day, 8))
        self.mince_n = self.receive(self.mince, self.north_shop, 0, cost='6.0000', when=at(day, 8), kg='10.000')
        self.rice_w = self.receive(self.rice, self.warehouse, 100, cost='1.4000', when=at(day, 9))
        self.free = self.receive(self.rice, self.south_shop, 5, cost=None, when=at(day, 9))  # no cost known
        self.out(self.rice_n, 4, Location.CUSTOMER, when=at(day + timedelta(days=2)))
        self.out(self.mince_n, 0, Location.CUSTOMER, when=at(day + timedelta(days=3)), kg='2.500')
        self.out(self.rice_n, 1, Location.WASTE, when=at(day + timedelta(days=4)))
        with clock(at(day + timedelta(days=5))):
            from django.db import transaction
            with transaction.atomic():
                lot = StockBatch.objects.select_for_update().get(pk=self.rice_w.pk)
                moved = transfer_batch(lot, 30, self.south_shop)
        StockBatch.objects.filter(pk=moved.pk).update(unit_cost=D('1.4000'))
        # This week: more sales, and a lot received after the instant we value at.
        self.out(self.rice_n, 5, Location.CUSTOMER, when=at(MONDAY + timedelta(days=1)))
        self.later = self.receive(self.rice, self.north_shop, 50, cost='1.6000', when=at(MONDAY + timedelta(days=3)))

    def by_code(self, rows):
        return {row['location_code']: row for row in rows}

    def test_value_at_the_end_of_last_week_matches_a_replay(self):
        end = at(MONDAY, 0)  # UK midnight starting this week
        rows = self.by_code(stock_value(self.company, end))
        # North: 15 rice × 1.50 + 7.5 kg × 6.00; warehouse 70 × 1.40; south 30 × 1.40 (+5 uncosted).
        self.assertEqual({code: row['value'] for code, row in rows.items()},
                         {self.north_shop.code: D('67.50'), 'WH-FAKE': D('98.00'), self.south_shop.code: D('42.00')})
        self.assertEqual(rows[self.south_shop.code]['uncosted_units'], 5)
        self.assertEqual(rows[self.north_shop.code]['branch_name'], 'Fake North')
        self.assertEqual({code: row['value'] for code, row in rows.items()}, replay_value(self.company, end))

    def test_value_mid_week_counts_movements_up_to_then_only(self):
        for moment in (at(MONDAY + timedelta(days=2)), at(MONDAY + timedelta(days=6), 23)):
            rows = self.by_code(stock_value(self.company, moment))
            self.assertEqual({code: row['value'] for code, row in rows.items()}, replay_value(self.company, moment))
        self.assertEqual(self.by_code(stock_value(self.company, at(MONDAY + timedelta(days=2))))
                         [self.north_shop.code]['value'], D('60.00'))  # the later lot is not there yet

    def test_only_this_company(self):
        from common.tests.factories import CompanyFactory

        self.assertEqual(stock_value(CompanyFactory(), at(MONDAY)), [])


class WeekBoundsTests(LedgerCase):
    def test_part_week_compares_the_same_days(self):
        wednesday = MONDAY + timedelta(days=2)
        self.assertEqual(week_bounds(MONDAY + timedelta(days=5), today=wednesday),
                         (MONDAY, wednesday, MONDAY - timedelta(days=7), wednesday - timedelta(days=7)))
        self.assertEqual(week_bounds(MONDAY - timedelta(days=3), today=wednesday),
                         (MONDAY - timedelta(days=7), MONDAY - timedelta(days=1), MONDAY - timedelta(days=14),
                          MONDAY - timedelta(days=8)))
