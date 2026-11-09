"""G53: automatic postings — till takings and voids, customer ledger documents, waste and butchery loss; re-running
posts nothing new; the trial balance balances."""

from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from accounting import postings
from accounting.journal import lock_period
from accounting.models import Account, Journal, JournalLine
from accounting.postings import run_nightly, run_postings
from accounting.reports import trial_balance
from inventory.models import Breakdown, BreakdownLine, StockBatch, WriteOff, YieldTemplate, YieldTemplateOutput
from sales.models import Sale, SaleItem, TillSession
from tenants.models import Branch, Till
from vat.services import UK
from wholesale.ledger import post_adjustment
from wholesale.models import LedgerEntry
from wholesale.payments import reverse_payment
from wholesale.tests.test_payments import PaymentCase

D = Decimal


def at(day, hour=12):
    return datetime.combine(day, time(hour), tzinfo=UK)


class PostingCase(PaymentCase):
    """``first`` (12.24) and ``second`` (8.24) are issued today; plus a branch with one till."""

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(company=self.company, name='Fake High Street', address='1 Fake Road',
                                            code='HS')
        self.till = Till.objects.create(branch=self.branch, number=1, device_id=f'fake-till-{self.company.pk}')
        self.session = TillSession.objects.create(till=self.till, opened_by=self.staff)

    def key(self, system_key):
        return Account.objects.get(company=self.company, system_key=system_key)

    def sale(self, method, lines, day=None, voided_on=None):
        """``lines``: [(vat_code, net, vat)] (gross = net + vat)."""
        with mock.patch('django.utils.timezone.now', return_value=at(day or self.today)):
            total = sum(net + vat for _code, net, vat in lines)
            sale = Sale.objects.create(session=self.session, cashier=self.staff, payment_method=method, total=total,
                                       amount_tendered=total)
            for code, net, vat in lines:
                SaleItem.objects.create(sale=sale, product=self.rice, product_name='Fake Rice', quantity=1,
                                        unit_price=net + vat, line_total=net + vat, vat_code=code or '',
                                        vat_rate=None if code is None else D('20.00') if vat else D('0.00'),
                                        net_amount=None if code is None else net,
                                        vat_amount=None if code is None else vat)
        if voided_on:
            Sale.objects.filter(pk=sale.pk).update(status=Sale.VOIDED, voided_at=at(voided_on, 15),
                                                   voided_by=self.staff, void_reason='Fake')
        return sale

    def lines_of(self, journal):
        return sorted((line.account.system_key or line.account.code, line.debit, line.credit)
                      for line in journal.lines.select_related('account'))

    def posted(self, source):
        return list(Journal.objects.filter(company=self.company, source=source).order_by('id'))

    def post_range(self, day=None, end=None):
        return run_postings(self.company, day or self.today, end or day or self.today)

    def write_off(self, cost, day=None):
        batch = StockBatch.objects.filter(product=self.rice).first()
        return WriteOff.objects.create(company=self.company, number=WriteOff.objects.count() + 1, batch=batch,
                                       location=batch.location, reason='expired', quantity=1, cost=cost,
                                       status=WriteOff.WRITTEN_OFF, written_off_at=at(day or self.today))

    def breakdown(self, input_cost, allocated, day=None, voided_on=None):
        batch = StockBatch.objects.filter(product=self.chops).first()
        template, _ = YieldTemplate.objects.get_or_create(company=self.company, name='Fake lamb',
                                                          input_product=self.chops)
        output, _ = YieldTemplateOutput.objects.get_or_create(template=template, product=self.chops,
                                                              defaults={'expected_percent': D('80.00')})
        with mock.patch('django.utils.timezone.now', return_value=at(day or self.today)):
            made = Breakdown.objects.create(company=self.company, number=Breakdown.objects.count() + 1,
                                            template=template, location=batch.location, input_batch=batch,
                                            input_kg=D('5.000'), loss_kg=D('0.500'), input_cost=input_cost)
        BreakdownLine.objects.create(breakdown=made, template_output=output, kind='product', product=self.chops,
                                     name='Fake chops', weight_kg=D('4.000'), allocated_cost=allocated)
        if voided_on:
            Breakdown.objects.filter(pk=made.pk).update(status=Breakdown.VOIDED, voided_at=at(voided_on, 16),
                                                        void_reason='Fake')
        return made


class TillPostingTests(PostingCase):
    def test_a_mixed_day_by_method_and_vat_code(self):
        self.sale('cash', [('zero', D('2.00'), D('0.00')), ('standard', D('1.00'), D('0.20'))])
        self.sale('card', [('standard', D('0.83'), D('0.17')), ('reduced', D('1.00'), D('0.05'))])
        self.post_range()
        [journal] = self.posted('till')
        self.assertEqual((journal.source_ref, journal.date), (f'{self.branch.pk}:{self.today}', self.today))
        self.assertEqual(self.lines_of(journal), sorted([
            ('cash', D('3.20'), D('0')), ('card_clearing', D('2.05'), D('0')),
            ('sales_zero', D('0'), D('2.00')), ('sales_standard', D('0'), D('1.83')),
            ('sales_reduced', D('0'), D('1.00')), ('vat_output', D('0'), D('0.42'))]))

    def test_voids_same_day_and_next_day(self):
        self.sale('cash', [('zero', D('5.00'), D('0'))], voided_on=self.today)
        self.sale('card', [('standard', D('10.00'), D('2.00'))], voided_on=self.today + timedelta(days=1))
        self.post_range(self.today, self.today + timedelta(days=1))
        takings, = self.posted('till')
        self.assertIn(('sales_standard', D('0'), D('10.00')), self.lines_of(takings))  # voided later: still sold
        same, next_day = self.posted('till_void')
        self.assertEqual((same.date, self.lines_of(same)), (self.today, sorted([
            ('cash', D('0'), D('5.00')), ('sales_zero', D('5.00'), D('0'))])))
        self.assertEqual(next_day.date, self.today + timedelta(days=1))
        self.assertEqual(self.lines_of(next_day), sorted([
            ('card_clearing', D('0'), D('12.00')), ('sales_standard', D('10.00'), D('0')),
            ('vat_output', D('2.00'), D('0'))]))

    def test_old_lines_without_vat_go_to_suspense(self):
        self.sale('cash', [(None, D('3.00'), D('0'))])
        self.post_range()
        [journal] = self.posted('till')
        self.assertIn(('suspense', D('0'), D('3.00')), self.lines_of(journal))
        self.assertIn('VAT not recorded', journal.narrative)

    def test_a_day_with_no_sales_posts_nothing(self):
        self.post_range()
        self.assertEqual(self.posted('till') + self.posted('till_void'), [])


class LedgerPostingTests(PostingCase):
    def test_an_invoice_by_its_lines(self):
        self.post_range()
        journals = self.posted('ledger')
        invoice_entry = LedgerEntry.objects.get(invoice=self.first)
        [journal] = [j for j in journals if j.source_ref == str(invoice_entry.pk)]
        lines = self.lines_of(journal)
        self.assertIn(('debtors', D('12.24'), D('0')), lines)
        self.assertEqual(sum(row[2] for row in lines), D('12.24'))
        self.assertEqual(journal.date, self.first.invoice_date)
        self.assertIn(self.first.number_display, journal.narrative)

    def test_payments_by_method_a_reversal_and_an_adjustment(self):
        cash = self.pay('5.00', method='cash')
        self.pay('3.00', method='card')
        self.pay('2.00', method='cheque')
        reverse_payment(cash, self.staff, 'Fake counterfeit')
        post_adjustment(self.customer, D('-1.50'), 'Fake goodwill', self.staff)
        self.post_range()
        by_kind = {}
        for journal in self.posted('ledger'):
            kind = LedgerEntry.objects.get(pk=int(journal.source_ref)).kind
            by_kind.setdefault(kind, []).append(self.lines_of(journal))
        self.assertIn(sorted([('cash', D('5.00'), D('0')), ('debtors', D('0'), D('5.00'))]), by_kind['payment'])
        self.assertIn(sorted([('card_clearing', D('3.00'), D('0')), ('debtors', D('0'), D('3.00'))]),
                      by_kind['payment'])
        self.assertIn(sorted([('bank', D('2.00'), D('0')), ('debtors', D('0'), D('2.00'))]), by_kind['payment'])
        self.assertEqual(by_kind['payment_reversal'], [sorted([('cash', D('0'), D('5.00')),
                                                               ('debtors', D('5.00'), D('0'))])])
        self.assertEqual(by_kind['adjustment'], [sorted([('debtors', D('0'), D('1.50')),
                                                         ('suspense', D('1.50'), D('0'))])])

    def test_debtors_equals_the_customer_ledger(self):
        self.pay('15.00')
        self.post_range()
        debtors = sum(line.debit - line.credit for line in JournalLine.objects.filter(account=self.key('debtors')))
        self.assertEqual(debtors, sum(entry.amount for entry in LedgerEntry.objects.filter(customer=self.customer)))


class StockPostingTests(PostingCase):
    def test_write_offs_with_a_cost(self):
        made = self.write_off(D('4.25'))
        self.write_off(None)  # no cost known: skipped
        WriteOff.objects.create(company=self.company, number=99, batch=made.batch, location=made.location,
                                reason='damaged', quantity=1, cost=D('9.00'), status=WriteOff.PENDING)
        summary = self.post_range()
        [journal] = self.posted('write_off')
        self.assertEqual((journal.source_ref, self.lines_of(journal)),
                         (str(made.pk), sorted([('wastage', D('4.25'), D('0')), ('purchases', D('0'), D('4.25'))])))
        self.assertEqual(summary['skipped'], 1)

    def test_breakdown_loss_and_its_void(self):
        self.breakdown(D('50.00'), D('41.30'), voided_on=self.today + timedelta(days=1))
        self.breakdown(None, None)  # no input cost: skipped
        self.breakdown(D('10.00'), D('10.00'))  # nothing lost at cost: nothing to post
        self.post_range(self.today, self.today + timedelta(days=1))
        [loss] = self.posted('breakdown')
        self.assertEqual(self.lines_of(loss), sorted([('yield_loss', D('8.70'), D('0')),
                                                      ('purchases', D('0'), D('8.70'))]))
        [void] = self.posted('breakdown_void')
        self.assertEqual((void.date, self.lines_of(void)), (self.today + timedelta(days=1), sorted([
            ('yield_loss', D('0'), D('8.70')), ('purchases', D('8.70'), D('0'))])))


class RunTests(PostingCase):
    def seed_day(self):
        self.sale('cash', [('zero', D('2.00'), D('0.00')), ('standard', D('1.00'), D('0.20'))])
        self.sale('card', [('standard', D('5.00'), D('1.00'))], voided_on=self.today)
        self.pay('15.00')
        self.write_off(D('3.10'))
        self.breakdown(D('20.00'), D('17.45'))

    def test_trial_balance_balances_for_a_seeded_day(self):
        self.seed_day()
        summary = self.post_range()
        self.assertGreaterEqual(summary['posted'], 6)
        balance = trial_balance(self.company, on=self.today)
        self.assertEqual(balance['debit'], balance['credit'])
        self.assertGreater(balance['debit'], 0)
        rows = {row['code']: row for row in balance['rows']}
        self.assertEqual(rows['1100']['debit'] - rows['1100']['credit'], D('5.48'))  # 20.48 invoiced - 15 paid
        self.assertNotIn('7000', rows)  # accounts with nothing posted are left out

    def test_rerun_creates_nothing_new(self):
        self.seed_day()
        first = self.post_range()
        count = Journal.objects.count()
        again = self.post_range()
        self.assertEqual(Journal.objects.count(), count)
        self.assertEqual((again['posted'], again['already']), (0, first['posted']))

    def test_earlier_unposted_documents_are_caught_up(self):
        old = self.today - timedelta(days=20)
        LedgerEntry.objects.create(customer=self.customer, kind='adjustment', entry_date=old, amount=D('2.00'),
                                   description='Debit adjustment', reason='Fake', created_by=self.staff)
        self.post_range()  # today only, but the older entry was never posted
        self.assertIn(old, [journal.date for journal in self.posted('ledger')])

    def test_a_locked_month_moves_to_the_first_open_day(self):
        old = (self.today.replace(day=1) - timedelta(days=1)).replace(day=10)  # last month
        LedgerEntry.objects.create(customer=self.customer, kind='adjustment', entry_date=old, amount=D('2.00'),
                                   description='Debit adjustment', reason='Fake', created_by=self.staff)
        lock_period(self.company, old.replace(day=1), self.staff)
        summary = self.post_range()
        [moved] = [j for j in self.posted('ledger') if 'dated' in j.narrative]
        self.assertEqual(moved.date, self.today.replace(day=1))
        self.assertIn(f'{old:%d/%m/%Y}', moved.narrative)
        self.assertEqual(summary['moved'], 1)

    def test_nightly_posts_yesterday_for_every_company(self):
        yesterday = self.today - timedelta(days=1)
        self.sale('cash', [('zero', D('2.00'), D('0.00'))], day=yesterday)
        self.sale('cash', [('zero', D('9.00'), D('0.00'))])  # today: not yet
        with mock.patch.object(postings, 'uk_date', return_value=self.today):
            run_nightly()
        [journal] = self.posted('till')
        self.assertEqual(journal.date, yesterday)

    def test_the_command(self):
        self.seed_day()
        call_command('post_journals', '--from', self.today.isoformat(), '--to', self.today.isoformat(),
                     '--company', str(self.company.pk), stdout=open('/dev/null', 'w'))
        self.assertTrue(self.posted('till'))

    def test_a_missing_system_account_stops_that_company_only(self):
        Account.objects.filter(company=self.company, system_key='wastage').update(system_key=None)
        self.write_off(D('1.00'))
        summary = self.post_range()
        self.assertEqual(Journal.objects.filter(company=self.company).count(), 0)
        self.assertIn('wastage', summary['error'])
        self.assertTrue(timezone.now())  # the run itself did not raise
