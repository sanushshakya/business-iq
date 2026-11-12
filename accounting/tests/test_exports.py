"""G55: the accountant's CSV exports — every journal line, and the lines behind the VAT boxes; their totals match the
reports."""

import csv
import io
from collections import defaultdict
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from accounting.journal import post_journal
from accounting.models import Account
from accounting.reports import profit_and_loss, trial_balance
from accounting.tests.test_vat_return import VatReturnCase
from accounting.vat_return import vat_boxes
from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK

D = Decimal


class ExportTests(VatReturnCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(UserFactory(company=self.company))  # members read
        self.sale('cash', [('zero', D('2.00'), D('0.00')), ('standard', D('10.00'), D('2.00'))])
        self.sale('card', [('standard', D('5.00'), D('1.00'))], voided_on=self.today)
        self.pay('15.00')
        self.write_off(D('3.10'))
        self.post_range()
        self.bill(D('100.00'), D('20.00'))
        post_journal(self.company, date=self.today, narrative='=Fake wages', user=self.staff, lines=[
            {'account': Account.objects.get(company=self.company, code='7000'), 'debit': D('50.00'),
             'description': '@payroll'},
            {'account': self.key('bank'), 'credit': D('50.00')}])
        self.start, self.end = self.month()
        self.range = {'from': self.start.isoformat(), 'to': self.end.isoformat()}

    def download(self, name, params=None):
        answer = self.api.get(reverse(name), params or self.range)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer['Content-Type'], 'text/csv; charset=utf-8')
        return list(csv.DictReader(io.StringIO(answer.content.decode())))

    def test_journal_lines_add_up_to_the_trial_balance_and_profit_and_loss(self):
        rows = self.download('export-journals')
        lines, total = rows[:-1], rows[-1]
        self.assertEqual(total['Journal'], 'Total')
        self.assertEqual(D(total['Debit']), D(total['Credit']))
        self.assertEqual(D(total['Debit']), sum(D(row['Debit']) for row in lines))
        net = defaultdict(lambda: D('0'))
        for row in lines:
            net[row['Account']] += D(row['Debit']) - D(row['Credit'])
        balance = trial_balance(self.company, on=self.end)  # nothing was posted before this month
        self.assertEqual({row['code']: row['debit'] - row['credit'] for row in balance['rows']},
                         {code: amount for code, amount in net.items() if amount})
        pl = profit_and_loss(self.company, self.start, self.end)
        self.assertEqual(sum(-net[row['code']] for row in pl['income']), pl['total_income'])
        self.assertEqual(sum(net[row['code']] for row in pl['expenses']), pl['total_expenses'])

    def test_rows_say_where_they_came_from_and_text_is_never_a_formula(self):
        rows = self.download('export-journals')
        self.assertEqual(list(rows[0]), ['Journal', 'Date', 'Source', 'Source ref', 'Narrative', 'Account',
                                         'Account name', 'Debit', 'Credit', 'Line description'])
        wages = [row for row in rows if row['Account'] == '7000']
        self.assertEqual((wages[0]['Narrative'], wages[0]['Line description']), ("'=Fake wages", "'@payroll"))
        self.assertIn('till', {row['Source'] for row in rows})

    def test_vat_lines_add_up_to_the_return(self):
        rows = self.download('export-vat')
        boxes = vat_boxes(self.company, self.start, self.end)
        totals = {row['Box']: D(row['Amount']) for row in rows if row['Journal'] == 'Total'}
        self.assertEqual(totals, {'1': boxes['box1'], '4': boxes['box4'], '6': totals['6'], '7': totals['7']})
        self.assertEqual(int(totals['6']), boxes['box6'])  # the return drops the pence
        self.assertEqual(int(totals['7']), boxes['box7'])
        for box in ('1', '4', '6', '7'):
            self.assertEqual(sum(D(row['Amount']) for row in rows if row['Box'] == box and row['Journal'] != 'Total'),
                             totals[box])
        self.assertNotIn('5100', {row['Account'] for row in rows})  # waste is not a purchase

    def test_range_roles_and_tenancy(self):
        for params in ({'from': self.end.isoformat(), 'to': self.start.isoformat()}, {'from': '2026-1-1'},
                       {'from': '2024-01-01', 'to': '2025-06-30'}):
            self.assertEqual(self.api.get(reverse('export-journals'), params).status_code, 400)
        stranger = APIClient()
        stranger.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True, is_superuser=True))
        rows = list(csv.DictReader(io.StringIO(stranger.get(reverse('export-journals'), self.range).content.decode())))
        self.assertEqual([row['Journal'] for row in rows], ['Total'])
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        picker = APIClient()
        picker.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        for name in ('export-journals', 'export-vat'):
            self.assertEqual(picker.get(reverse(name), self.range).status_code, 403)
