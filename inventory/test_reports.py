# inventory/test_reports.py

"""G24: yield (actual vs expected, by product, supplier and butcher) and waste (by reason or product, per UK week)
reports, whose totals must match the stock ledger."""

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory import reports
from inventory.ledger import move, virtual_location
from inventory.breakdowns import void_breakdown
from inventory.models import Location, StockBatch, StockMovement, Supplier, WriteOff
from inventory.test_breakdowns import BreakdownTestCase
from inventory.write_offs import approve, raise_write_off, reject
from tenants.roles import STOCK, TILL

D = Decimal


class ReportTestCase(BreakdownTestCase):
    """Two completed breakdowns (two butchers; one from a supplier, one with none recorded), one voided; write-offs of each
    status; and another company's breakdown and write-off that must never show."""

    def setUp(self):
        super().setUp()
        self.butcher = UserFactory(company=self.company, role=STOCK, name='Fake Butcher')
        self.manager = UserFactory(company=self.company, is_staff=True, name='Fake Manager')
        self.farm = Supplier.objects.create(company=self.company, name='Fake Farms Ltd')
        StockBatch.objects.filter(pk=self.input.pk).update(supplier=self.farm, unit_cost=D('5.0000'))
        self.input.refresh_from_db()
        # 1: the user, Fake Farms, 10 kg: steak 3, mince 4.5, bone 1.5, fat 0.65, loss 0.35
        self.first, _ = self.record()
        # 2: the butcher, no supplier and no cost, 5 kg: steak 1.6, mince 2.2, bone 0.8, fat 0.4 (no loss)
        self.second_batch = self.batch(self.side, kg='5.000', days=6, number='SIDE-2')
        self.second, _ = self.record(
            input_batch=self.second_batch, input_kg=D('5.000'), user=self.butcher, loss_reason='',
            lines=[(self.out_steak, '1.600'), (self.out_mince, '2.200'), (self.out_bone, '0.800'),
                   (self.out_fat, '0.400')])
        # voided: must not count
        voided_batch = self.batch(self.side, kg='4.000', days=6, number='SIDE-3')
        voided, _ = self.record(input_batch=voided_batch, input_kg=D('4.000'), loss_reason='',
                                lines=[(self.out_steak, '1.200'), (self.out_mince, '1.800'), (self.out_bone, '0.600'),
                                       (self.out_fat, '0.400')])
        void_breakdown(voided, user=self.user, reason='Fake: wrong batch')
        # write-offs on the shop floor
        self.rice = ProductFactory(company=self.company, name='Fake Rice', price=D('2.00'), stock_quantity=0)
        self.rice_batch = self.stock(self.rice, pieces=10, cost='0.8000')
        self.chicken = self.weighed('Fake Chicken')
        self.chicken_batch = self.stock(self.chicken, kg='3.000', cost='4.0000')
        self.loose_nocost = self.stock(self.chicken, kg='2.000', number='NOCOST')
        self.wo_rice = self.write(self.rice_batch, 'damaged', quantity=3)  # £2.40, written off at once
        self.wo_chicken = self.write(self.chicken_batch, 'expired', weight_kg=D('1.250'))  # £5.00
        self.wo_nocost = self.write(self.loose_nocost, 'expired', weight_kg=D('0.500'))  # no cost: pending
        approve(self.wo_nocost, user=self.manager)
        self.write(self.loose_nocost, 'quality', weight_kg=D('0.300'))  # no cost: stays pending
        rejected = self.write(self.loose_nocost, 'quality', weight_kg=D('0.250'))
        reject(rejected, user=self.manager)
        # another company
        self.other = CompanyFactory()
        theirs = ProductFactory(company=self.other, name='Fake Their Rice', stock_quantity=0)
        their_batch = StockBatchFactory(product=theirs, quantity=0, unit_cost=D('1.0000'))
        with transaction.atomic():
            move(their_batch, 5, 'inbound', counterpart=virtual_location(self.other, Location.SUPPLIER))
        raise_write_off(company=self.other, user=UserFactory(company=self.other, is_staff=True),
                        batch=their_batch, reason='damaged', quantity=2)
        self.week = self.today - timedelta(days=self.today.weekday())

    def stock(self, product, *, pieces=0, kg=None, cost=None, number=None):
        batch = StockBatchFactory(product=product, quantity=0, weight_kg=D('0'), location=self.shop,
                                  expiration_date=self.today + timedelta(days=5), unit_cost=cost and D(cost),
                                  **({'batch_number': number} if number else {}))
        with transaction.atomic():
            move(batch, pieces, 'inbound', counterpart=virtual_location(self.company, Location.SUPPLIER),
                 weight_kg=None if kg is None else D(kg))
        return StockBatch.objects.get(pk=batch.pk)

    def write(self, batch, reason, **amount):
        return raise_write_off(company=self.company, user=self.user, batch=batch, reason=reason, **amount)

    def yield_rows(self, group='product'):
        return reports.yield_report(self.company, self.today, self.today, group)['rows']

    def waste_weeks(self, group='reason'):
        return reports.waste_report(self.company, self.today, self.today, group)['weeks']


class YieldReportTests(ReportTestCase):
    def test_yield_totals_match_ledger(self):
        [row] = self.yield_rows()
        moves = StockMovement.objects.filter(breakdown__company=self.company, breakdown__status='completed')
        taken = moves.filter(movement_type='outbound', batch__product=self.side).aggregate(kg=Sum('weight_kg'))['kg']
        self.assertEqual(row['input_kg'], taken)
        into_outputs = moves.filter(movement_type='inbound').aggregate(kg=Sum('weight_kg'))['kg']
        to_waste = moves.filter(destination_location=self.waste).aggregate(kg=Sum('weight_kg'))['kg']
        to_loss = moves.filter(destination_location=self.adjustment).aggregate(kg=Sum('weight_kg'))['kg']
        products = sum(o['actual_kg'] for o in row['outputs'] if o['kind'] == 'product')
        waste = sum(o['actual_kg'] for o in row['outputs'] if o['kind'] == 'waste')
        self.assertEqual((products, waste, row['loss_kg']), (into_outputs, to_waste, to_loss))
        self.assertEqual(products + waste + row['loss_kg'], row['input_kg'])

    def test_by_product_actual_vs_expected(self):
        [row] = self.yield_rows()
        self.assertEqual((row['name'], row['breakdowns'], row['input_kg']), ('Fake Side of Beef', 2, D('15.000')))
        steak = next(o for o in row['outputs'] if o['name'] == 'Fake Rump Steak')
        self.assertEqual(steak['actual_kg'], D('4.600'))
        self.assertEqual(steak['expected_kg'], D('4.500'))  # 30% of 10 + 30% of 5
        self.assertEqual(steak['variance_kg'], D('0.100'))
        self.assertEqual((steak['actual_percent'], steak['expected_percent']), (D('30.67'), D('30.00')))
        self.assertEqual((row['loss_kg'], row['expected_loss_kg']), (D('0.350'), D('0.450')))
        self.assertEqual(row['input_cost'], D('50.00'))  # only the first had a cost
        self.assertEqual([o['kind'] for o in row['outputs']], ['product', 'product', 'waste', 'waste'])

    def test_by_supplier(self):
        rows = {row['name']: row for row in self.yield_rows('supplier')}
        self.assertEqual(set(rows), {'Fake Farms Ltd', 'Supplier not recorded'})
        self.assertEqual(rows['Fake Farms Ltd']['input_kg'], D('10.000'))
        self.assertIsNone(rows['Supplier not recorded']['key'])
        self.assertIsNone(rows['Supplier not recorded']['input_cost'])

    def test_by_butcher(self):
        rows = {row['name']: row for row in self.yield_rows('butcher')}
        self.assertEqual(rows['Fake Butcher']['input_kg'], D('5.000'))
        self.assertEqual(rows[self.user.email]['input_kg'], D('10.000'))

    def test_voided_breakdown_excluded(self):
        [row] = self.yield_rows()
        self.assertEqual(row['breakdowns'], 2)

    def test_dates_outside_range_excluded(self):
        result = reports.yield_report(self.company, self.today - timedelta(days=9), self.today - timedelta(days=1),
                                      'product')
        self.assertEqual(result['rows'], [])


class WasteReportTests(ReportTestCase):
    def test_waste_totals_match_ledger(self):
        [week] = self.waste_weeks('product')
        moves = StockMovement.objects.filter(write_off__company=self.company, destination_location=self.waste)
        totals = moves.aggregate(units=Sum('quantity'), kg=Sum('weight_kg'))
        self.assertEqual(sum(r['quantity'] for r in week['rows']), totals['units'])
        self.assertEqual(sum(r['weight_kg'] for r in week['rows']), totals['kg'])
        self.assertEqual(sum(r['write_offs'] for r in week['rows']),
                         moves.aggregate(n=Count('write_off', distinct=True))['n'])

    def test_by_reason(self):
        [week] = self.waste_weeks()
        self.assertEqual(week['week_start'], self.week)
        rows = {row['key']: row for row in week['rows']}
        self.assertEqual(set(rows), {'damaged', 'expired'})
        self.assertEqual((rows['damaged']['name'], rows['damaged']['quantity'], rows['damaged']['cost']),
                         ('Damaged', 3, D('2.40')))
        self.assertEqual((rows['expired']['weight_kg'], rows['expired']['cost'], rows['expired']['write_offs']),
                         (D('1.750'), D('5.00'), 2))
        self.assertEqual(week['total_cost'], D('7.40'))

    def test_only_written_off_counted(self):
        [week] = self.waste_weeks()
        self.assertEqual(sum(r['write_offs'] for r in week['rows']), 3)  # not the pending or the rejected one

    def test_uncosted_counted(self):
        [week] = self.waste_weeks()
        expired = next(r for r in week['rows'] if r['key'] == 'expired')
        self.assertEqual(expired['uncosted'], 1)

    def test_week_uses_uk_date(self):
        # 23:30 UTC on Sunday 5 July 2026 is 00:30 on Monday 6 July in the UK: the week of the 6th
        WriteOff.objects.filter(pk=self.wo_rice.pk).update(
            written_off_at=datetime(2026, 7, 5, 23, 30, tzinfo=dt_timezone.utc))
        monday = date(2026, 7, 6)
        self.assertEqual(reports.waste_report(self.company, date(2026, 7, 5), date(2026, 7, 5), 'reason')['weeks'], [])
        [week] = reports.waste_report(self.company, monday, monday, 'reason')['weeks']
        self.assertEqual((week['week_start'], week['total_cost']), (monday, D('2.40')))

    def test_reports_scoped_to_company(self):
        [week] = reports.waste_report(self.other, self.today, self.today, 'product')['weeks']
        self.assertEqual([r['name'] for r in week['rows']], ['Fake Their Rice'])
        self.assertEqual(reports.yield_report(self.other, self.today, self.today, 'product')['rows'], [])
        names = {r['name'] for r in self.waste_weeks('product')[0]['rows']}
        self.assertNotIn('Fake Their Rice', names)


class ReportApiTests(ReportTestCase):
    def get(self, name, user=None, **params):
        api = APIClient()
        api.force_authenticate(user or self.manager)
        return api.get(reverse(f'report-{name}'), params)

    def test_yield_api(self):
        response = self.get('yield', group='supplier')
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body['group'], body['to']), ('supplier', f'{self.today}'))
        self.assertEqual(body['from'], f'{self.week - timedelta(weeks=7)}')  # the last 8 weeks
        self.assertEqual({r['name'] for r in body['rows']}, {'Fake Farms Ltd', 'Supplier not recorded'})

    def test_waste_api(self):
        response = self.get('waste', **{'from': f'{self.today}', 'to': f'{self.today}'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['total_cost'], '7.40')

    def test_stock_role_reads_and_till_refused(self):
        self.assertEqual(self.get('yield', user=self.butcher).status_code, 200)
        self.assertEqual(self.get('waste', user=UserFactory(company=self.company, role=TILL)).status_code, 403)
        self.assertEqual(self.get('waste', user=UserFactory(company=self.company)).status_code, 403)

    def test_bad_params_are_400(self):
        self.assertEqual(self.get('yield', group='colour').status_code, 400)
        self.assertEqual(self.get('waste', **{'from': '2025-01-01', 'to': '2026-06-01'}).status_code, 400)
        self.assertEqual(self.get('waste', **{'from': '2026-06-02', 'to': '2026-06-01'}).status_code, 400)

