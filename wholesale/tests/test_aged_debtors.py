"""G50: aged debtors — open debits by days past due, money on account, as at any date; totals are the balances."""

import csv
import io
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK
from vat.services import UK
from wholesale.aged_debtors import report
from wholesale.credit_notes import create_credit_note, issue_credit_note
from wholesale.ledger import balance, post_adjustment
from wholesale.models import LedgerEntry, TradeCustomer
from wholesale.payments import allocate, release, reverse_payment
from wholesale.returns import record_return
from wholesale.runs import complete_run, deliver, start_run
from wholesale.tests.test_payments import PaymentCase

D = Decimal
BUCKETS = ('current', 'days_1_30', 'days_31_60', 'days_61_90', 'days_90_plus')


def on_day(day):
    """``timezone.now()`` at noon UK on ``day``: rows made inside get that as ``created_at``."""
    return mock.patch('django.utils.timezone.now', return_value=datetime.combine(day, time(12), tzinfo=UK))


class AgedDebtorsBase(PaymentCase):
    """``first`` (12.24) and ``second`` (8.24) are issued today, due ``today + terms``."""

    def setUp(self):
        super().setUp()
        self.due = self.first.due_date

    def charge(self, amount, entry_date):
        return LedgerEntry.objects.create(customer=self.customer, kind=LedgerEntry.ADJUSTMENT, entry_date=entry_date,
                                          amount=D(amount), description='Debit adjustment', reason='Fake charge',
                                          created_by=self.staff)

    def row(self, as_at, customer=None):
        rows = {row['customer']: row for row in report(self.company.pk, as_at)['customers']}
        return rows.get((customer or self.customer).pk)

    def buckets(self, as_at):
        row = self.row(as_at)
        return tuple(row[name] for name in BUCKETS) + (row['on_account'], row['total'])

    def assert_total_is_balance(self, as_at):
        result = report(self.company.pk, as_at)
        for row in result['customers']:
            customer = TradeCustomer.objects.get(pk=row['customer'])
            self.assertEqual(sum(row[name] for name in BUCKETS) + row['on_account'], row['total'])
            self.assertEqual(row['total'], balance(customer, on=as_at))
        for name in BUCKETS + ('on_account', 'total'):
            self.assertEqual(result['totals'][name], sum((row[name] for row in result['customers']), D('0.00')))



class AgedDebtorsTests(AgedDebtorsBase):
    def test_buckets_by_days_past_due(self):
        for days, expected in ((0, 0), (1, 1), (30, 1), (31, 2), (60, 2), (61, 3), (90, 3), (91, 4), (400, 4)):
            with self.subTest(days=days):
                amounts = self.buckets(self.due + timedelta(days=days))
                self.assertEqual(amounts[expected], D('20.48'))
                self.assertEqual(sum(amounts[:5]), D('20.48'))
                self.assertEqual(amounts[5:], (D('0.00'), D('20.48')))

    def test_a_charge_is_due_on_its_date_and_nothing_after_as_at_counts(self):
        self.charge('5.00', self.today - timedelta(days=45))
        self.assertEqual(self.buckets(self.today),
                         (D('20.48'), D('0'), D('5.00'), D('0'), D('0'), D('0.00'), D('25.48')))
        before = self.today - timedelta(days=1)
        self.assertEqual(self.buckets(before)[2], D('5.00'))
        self.assertEqual(self.buckets(before)[6], D('5.00'))  # the invoices are dated today
        self.assertIsNone(self.row(self.today - timedelta(days=46)))
        self.assert_total_is_balance(before)

    def test_part_paid_oldest_first_and_overpaid(self):
        self.pay('15.00')
        self.assertEqual(self.buckets(self.today), (D('5.48'), 0, 0, 0, 0, D('0.00'), D('5.48')))
        self.pay('30.00')
        self.assertEqual(self.buckets(self.today), (0, 0, 0, 0, 0, D('-24.52'), D('-24.52')))
        self.assert_total_is_balance(self.today)

    def test_as_at_before_a_payment_was_allocated(self):
        old = self.today - timedelta(days=50)
        charge = self.charge('9.00', old)
        with on_day(self.today - timedelta(days=10)):
            self.pay('9.00', received_on=self.today - timedelta(days=10), allocate=None)  # on account first
        with on_day(self.today - timedelta(days=5)):
            allocate(LedgerEntry.objects.get(kind='payment'), self.staff, [{'debit': charge.pk, 'amount': D('9.00')}])
        self.assertEqual(self.buckets(self.today - timedelta(days=11))[2], D('9.00'))  # not yet paid
        on_account = self.buckets(self.today - timedelta(days=7))
        self.assertEqual((on_account[2], on_account[5], on_account[6]), (D('9.00'), D('-9.00'), D('0.00')))
        self.assertIsNone(self.row(self.today - timedelta(days=5)))  # all paid, and the invoices are dated today
        self.assertEqual(self.buckets(self.today)[2], 0)
        for days in (11, 7, 5, 0):
            self.assert_total_is_balance(self.today - timedelta(days=days))

    def test_a_payment_dated_back_counts_from_when_it_was_recorded(self):
        old = self.today - timedelta(days=40)
        self.charge('6.00', old)
        self.pay('6.00', received_on=self.today - timedelta(days=3))  # recorded today, allocated today
        three_days_ago = self.buckets(self.today - timedelta(days=3))
        self.assertEqual((three_days_ago[2], three_days_ago[5], three_days_ago[6]), (D('6.00'), D('-6.00'),
                                                                                     D('0.00')))
        self.assertEqual(self.buckets(self.today)[2], 0)
        self.assert_total_is_balance(self.today - timedelta(days=3))

    def test_released_allocation_is_open_again_from_the_day_it_was_released(self):
        later = self.today + timedelta(days=2)
        payment = self.pay('12.24')
        with on_day(later):
            release(payment.ledger_entries.get(kind='payment').allocations_out.get(), self.staff)
        self.assertEqual(self.buckets(self.today + timedelta(days=1))[0], D('8.24'))
        self.assertEqual(self.buckets(later)[0], D('20.48'))
        self.assertEqual(self.buckets(later)[5], D('-12.24'))
        self.assert_total_is_balance(later)

    def test_a_reversed_payment_leaves_the_invoices_open(self):
        payment = self.pay('20.48')
        reverse_payment(payment, self.staff, 'Fake bounced cheque')
        self.assertEqual(self.buckets(self.today), (D('20.48'), 0, 0, 0, 0, D('0.00'), D('20.48')))
        self.assert_total_is_balance(self.today)

    def test_customers_owing_nothing_are_left_out_and_others_are_by_account_code(self):
        quiet = TradeCustomer.objects.create(company=self.company, account_code='A-0', legal_name='Fake Quiet Ltd')
        early = TradeCustomer.objects.create(company=self.company, account_code='A-1', legal_name='Fake Early Ltd')
        LedgerEntry.objects.create(customer=early, kind=LedgerEntry.ADJUSTMENT, entry_date=self.today,
                                   amount=D('1.00'), description='Debit adjustment', reason='Fake', created_by=self.staff)
        codes = [row['account_code'] for row in report(self.company.pk, self.today)['customers']]
        self.assertNotIn(quiet.account_code, codes)
        self.assertEqual(codes, sorted(codes))
        self.assertIn('A-1', codes)
        self.assert_total_is_balance(self.today)

    def test_only_this_company(self):
        other = CompanyFactory()
        theirs = TradeCustomer.objects.create(company=other, account_code='X-1', legal_name='Fake Other Ltd')
        LedgerEntry.objects.create(customer=theirs, kind=LedgerEntry.ADJUSTMENT, entry_date=self.today,
                                   amount=D('7.00'), description='Debit adjustment', reason='Fake', created_by=self.staff)
        self.assertIsNone(self.row(self.today, theirs))
        self.assertEqual(report(self.company.pk, self.today)['totals']['total'], D('20.48'))


    def test_a_credit_note_on_a_paid_invoice_is_on_account(self):
        self.pay('8.24', allocate=[{'debit': self.entry(self.second).pk, 'amount': D('8.24')}])
        order = self.second.order
        run = self.plan(order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        complete_run(run, self.staff)
        returned = record_return(order, self.staff, [
            {'line': self.line(order, self.rice).pk, 'reason': 'not_wanted', 'quantity': 1}])
        issue_credit_note(create_credit_note(returned, self.staff), self.staff)  # 2.00
        self.assertEqual(self.buckets(self.today), (D('12.24'), 0, 0, 0, 0, D('-2.00'), D('10.24')))
        self.assert_total_is_balance(self.today)

    def test_a_credit_adjustment_pays_the_oldest(self):
        post_adjustment(self.customer, D('-15.00'), 'Fake goodwill', self.staff)
        self.assertEqual(self.buckets(self.due + timedelta(days=1)), (0, D('5.48'), 0, 0, 0, D('0.00'), D('5.48')))
        self.assert_total_is_balance(self.today)


class AgedDebtorsApiTests(AgedDebtorsBase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.url = reverse('tradecustomer-aged-debtors')

    def test_json(self):
        data = self.api.get(self.url, {'as_at': (self.due + timedelta(days=31)).isoformat()}).json()
        self.assertEqual(data['as_at'], (self.due + timedelta(days=31)).isoformat())
        [row] = data['customers']
        self.assertEqual((row['account_code'], row['days_31_60'], row['total']), (self.customer.account_code,
                                                                                  '20.48', '20.48'))
        self.assertEqual(data['totals']['total'], '20.48')

    def test_defaults_to_today_and_refuses_a_bad_date(self):
        self.assertEqual(self.api.get(self.url).json()['as_at'], self.today.isoformat())
        for bad in ('2026-13-01', '20261101', 'soon'):
            with self.subTest(bad=bad):
                self.assertEqual(self.api.get(self.url, {'as_at': bad}).status_code, 400)
        self.assertEqual(self.api.get(self.url, {'download': 'pdf'}).status_code, 400)

    def test_csv(self):
        TradeCustomer.objects.filter(pk=self.customer.pk).update(legal_name='=HYPERLINK("http://example.test")')
        answer = self.api.get(self.url, {'download': 'csv', 'as_at': self.today.isoformat()})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn(f'aged-debtors-{self.today.isoformat()}.csv', answer['Content-Disposition'])
        rows = list(csv.reader(io.StringIO(answer.content.decode())))
        self.assertEqual(rows[0], ['Account', 'Customer', 'Current', '1-30 days', '31-60 days', '61-90 days',
                                   '90+ days', 'On account', 'Total'])
        self.assertEqual(rows[1], [self.customer.account_code, "'=HYPERLINK(\"http://example.test\")", '20.48',
                                   '0.00', '0.00', '0.00', '0.00', '0.00', '20.48'])
        self.assertEqual(rows[-1][0], 'Total')
        self.assertEqual(rows[-1][-1], '20.48')

    def test_members_read_operators_and_strangers_do_not(self):
        member = APIClient()
        member.force_authenticate(UserFactory(company=self.company))
        self.assertEqual(member.get(self.url).status_code, 200)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        picker = APIClient()
        picker.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(picker.get(self.url).status_code, 403)
        stranger = APIClient()
        stranger.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True, is_superuser=True))
        self.assertEqual(stranger.get(self.url).json()['customers'], [])
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    def test_a_user_with_no_company_is_refused_not_shown_an_empty_report(self):
        nobody = APIClient()
        nobody.force_authenticate(UserFactory(company=None, is_staff=True, is_superuser=True))
        self.assertEqual(nobody.get(self.url).status_code, 403)
