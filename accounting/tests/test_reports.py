"""G54: profit and loss and the balance sheet from the journal; they agree with what was sold and invoiced."""

from datetime import date
from decimal import Decimal

from accounting.journal import post_journal
from accounting.models import Account
from accounting.reports import balance_sheet, profit_and_loss
from accounting.tests.test_postings import PostingCase
from wholesale.models import InvoiceLine

D = Decimal


class ReportTests(PostingCase):
    def seed(self):
        self.sale('cash', [('zero', D('2.00'), D('0.00')), ('standard', D('1.00'), D('0.20'))])
        self.pay('15.00')
        self.write_off(D('3.10'))
        self.post_range()
        post_journal(self.company, date=self.today, narrative='Fake wages', user=self.staff, lines=[
            {'account': Account.objects.get(company=self.company, code='7000'), 'debit': D('100.00')},
            {'account': self.key('bank'), 'credit': D('100.00')}])

    def test_profit_and_loss_matches_the_sales_and_invoices(self):
        self.seed()
        report = profit_and_loss(self.company, self.today.replace(day=1), self.today)
        invoiced = sum(InvoiceLine.objects.filter(invoice__in=[self.first, self.second]).values_list('net', flat=True))
        self.assertEqual(report['total_income'], D('3.00') + invoiced)
        expenses = {row['code']: row['amount'] for row in report['expenses']}
        self.assertEqual(expenses, {'5100': D('3.10'), '5000': D('-3.10'), '7000': D('100.00')})
        self.assertEqual(report['total_expenses'], D('100.00'))
        self.assertEqual(report['net_profit'], report['total_income'] - D('100.00'))
        self.assertEqual(profit_and_loss(self.company, date(2020, 1, 1), date(2020, 1, 31))['net_profit'], D('0.00'))

    def test_the_balance_sheet_balances(self):
        self.seed()
        sheet = balance_sheet(self.company, self.today)
        self.assertEqual(sheet['total_assets'], sheet['total_liabilities_and_equity'])
        assets = {row['code']: row['amount'] for row in sheet['assets']}
        self.assertEqual(assets['1100'], D('5.48'))  # debtors: 20.48 invoiced, 15 paid
        self.assertEqual(assets['1000'], D('15.00') - D('100.00'))  # paid in, wages out
        profit = [row for row in sheet['equity'] if row['code'] is None][0]
        self.assertEqual(profit['amount'], profit_and_loss(self.company, date(2000, 1, 1), self.today)['net_profit'])
        liabilities = {row['code']: row['amount'] for row in sheet['liabilities']}
        self.assertEqual(liabilities['2200'], D('0.20') + self.first.vat_total + self.second.vat_total)

