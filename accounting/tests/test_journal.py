"""G52: journals balance, are numbered, never change; months lock (and everything before them) and reopen."""

import importlib
from datetime import date
from decimal import Decimal

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.journal import (
    JournalError,
    account_balance,
    lock_period,
    locked_through,
    post_journal,
    reopen_period,
    reverse_journal,
)
from accounting.models import Account, Journal, JournalLine, Period, PeriodEvent
from common.tests.factories import CompanyFactory, UserFactory

D = Decimal
JUNE = date(2026, 6, 15)


class JournalCase(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.bank = self.account('bank')
        self.sales = self.account('sales_zero')
        self.vat = self.account('vat_output')

    def account(self, key, company=None):
        return Account.objects.get(company=company or self.company, system_key=key)

    def post(self, lines=None, day=JUNE, **fields):
        lines = lines if lines is not None else [
            {'account': self.bank, 'debit': D('12.00')},
            {'account': self.sales, 'credit': D('10.00')},
            {'account': self.vat, 'credit': D('2.00'), 'description': 'Fake VAT'},
        ]
        return post_journal(self.company, date=day, narrative=fields.pop('narrative', 'Fake takings'), lines=lines,
                            user=self.staff, **fields)


class PostingTests(JournalCase):
    def test_a_balanced_journal_is_numbered_and_kept(self):
        first = self.post()
        second = self.post()
        self.assertEqual((first.number, second.number, first.source, first.created_by), (1, 2, 'manual', self.staff))
        self.assertEqual([(line.account.code, line.debit, line.credit) for line in first.lines.all()],
                         [('1000', D('12.00'), D('0')), ('4020', D('0'), D('10.00')), ('2200', D('0'), D('2.00'))])
        self.assertEqual(account_balance(self.bank), D('24.00'))  # debit - credit
        self.assertEqual(account_balance(self.sales), D('-20.00'))
        self.assertEqual(account_balance(self.bank, on=date(2026, 6, 14)), D('0.00'))
        self.assertEqual(Period.objects.get(company=self.company).start, date(2026, 6, 1))

    def test_unbalanced_is_refused(self):
        for lines in (
            [{'account': self.bank, 'debit': D('12.00')}, {'account': self.sales, 'credit': D('11.99')}],
            [{'account': self.bank, 'debit': D('12.00')}, {'account': self.sales, 'credit': D('12.01')}],
            [{'account': self.bank, 'debit': D('0.01')}],
            [],
        ):
            with self.subTest(lines=lines), self.assertRaises(JournalError):
                self.post(lines)
        self.assertFalse(Journal.objects.exists())
        self.assertFalse(JournalLine.objects.exists())

    def test_bad_lines_are_refused_and_nothing_is_written(self):
        other = self.account('bank', CompanyFactory())
        Account.objects.filter(pk=self.vat.pk).update(is_active=False)
        for bad in (
            {'account': self.sales, 'debit': D('1.00'), 'credit': D('1.00')},
            {'account': self.sales},
            {'account': self.sales, 'credit': D('1.005')},
            {'account': self.sales, 'credit': D('-1.00')},
            {'account': self.sales, 'credit': 1},  # not a Decimal
            {'account': self.sales, 'credit': D('NaN')},
            {'account': self.sales, 'credit': D('10000000000000.00')},
            {'account': other, 'credit': D('1.00')},
            {'account': self.vat, 'credit': D('1.00')},
        ):
            with self.subTest(bad=bad), self.assertRaises(JournalError):
                self.post([{'account': self.bank, 'debit': D('1.00')}, bad])
        for narrative in ('', '   ', 'x' * 256):
            with self.subTest(narrative=narrative), self.assertRaises(JournalError):
                self.post(narrative=narrative)
        self.assertFalse(Journal.objects.exists())

    def test_a_source_ref_posts_once(self):
        first = self.post(source='fake_source', source_ref='DOC-1')
        again = self.post(source='fake_source', source_ref='DOC-1')
        self.assertEqual((again.pk, again.repeated, first.repeated), (first.pk, True, False))
        self.assertEqual(self.post(source='other_source', source_ref='DOC-1').number, 2)
        with self.assertRaises(JournalError):  # the same ref with different lines is a mistake, not a retry
            self.post([{'account': self.bank, 'debit': D('1.00')}, {'account': self.sales, 'credit': D('1.00')}],
                      source='fake_source', source_ref='DOC-1')

    def test_journals_and_lines_never_change(self):
        journal = self.post()
        line = journal.lines.first()
        for attempt in (lambda: journal.save(), lambda: journal.delete(), lambda: line.save(), lambda: line.delete(),
                        lambda: Journal.objects.update(narrative='x'), lambda: JournalLine.objects.all().delete(),
                        lambda: Journal.objects.bulk_update([journal], ['narrative'])):
            with self.assertRaises(ValidationError):
                attempt()
        self.assertEqual(JournalLine.objects.count(), 3)

    def test_the_chart_migration_keeps_used_accounts(self):
        self.post()
        migration = importlib.import_module('accounting.migrations.0002_default_chart')
        migration.remove_default_chart(django_apps, None)
        self.assertEqual(set(Account.objects.filter(company=self.company).values_list('code', flat=True)),
                         {'1000', '4020', '2200'})


class ReversalTests(JournalCase):
    def test_a_reversal_swaps_every_line(self):
        journal = self.post()
        reversal = reverse_journal(journal, self.staff, 'Fake keyed twice', date=date(2026, 6, 20))
        self.assertEqual((reversal.reverses, reversal.reason, reversal.date, reversal.number),
                         (journal, 'Fake keyed twice', date(2026, 6, 20), 2))
        self.assertEqual([(line.account_id, line.debit, line.credit) for line in reversal.lines.all()],
                         [(self.bank.pk, D('0'), D('12.00')), (self.sales.pk, D('10.00'), D('0')),
                          (self.vat.pk, D('2.00'), D('0'))])
        for account in (self.bank, self.sales, self.vat):
            self.assertEqual(account_balance(account), D('0.00'))

    def test_once_only_never_a_reversal_and_with_a_reason(self):
        journal = self.post()
        with self.assertRaises(JournalError):
            reverse_journal(journal, self.staff, '  ')
        reversal = reverse_journal(journal, self.staff, 'Fake', date=JUNE)
        for target in (journal, reversal):
            with self.subTest(target=target.number), self.assertRaises(JournalError):
                reverse_journal(target, self.staff, 'Fake again', date=JUNE)

    def test_a_reversal_into_an_inactive_account_is_still_allowed(self):
        journal = self.post()
        Account.objects.filter(pk=self.vat.pk).update(is_active=False)  # undoing must stay possible
        self.assertEqual(reverse_journal(journal, self.staff, 'Fake', date=JUNE).lines.count(), 3)


class PeriodTests(JournalCase):
    def test_posting_into_a_locked_month_is_refused(self):
        self.post(day=date(2026, 3, 31))
        lock_period(self.company, date(2026, 6, 1), self.staff)
        self.assertEqual(locked_through(self.company), date(2026, 6, 1))
        for day in (date(2026, 6, 30), date(2026, 6, 1), date(2026, 3, 31), date(2025, 1, 1)):  # no row for 2025
            with self.subTest(day=day), self.assertRaises(JournalError):
                self.post(day=day)
        self.assertEqual(self.post(day=date(2026, 7, 1)).number, 2)

    def test_a_reversal_into_a_locked_month_is_refused(self):
        journal = self.post()
        lock_period(self.company, date(2026, 6, 1), self.staff)
        with self.assertRaises(JournalError):
            reverse_journal(journal, self.staff, 'Fake', date=JUNE)
        self.assertEqual(reverse_journal(journal, self.staff, 'Fake', date=date(2026, 7, 2)).number, 2)

    def test_locking_locks_every_earlier_month_and_reopening_every_later_one(self):
        self.post(day=date(2026, 2, 10))
        lock_period(self.company, date(2026, 4, 1), self.staff)
        self.assertEqual(list(Period.objects.filter(company=self.company).values_list('start', 'locked')),
                         [(date(2026, 2, 1), True), (date(2026, 3, 1), True), (date(2026, 4, 1), True)])
        lock_period(self.company, date(2026, 2, 1), self.staff)  # already locked: no change, nothing reopened
        self.assertEqual(locked_through(self.company), date(2026, 4, 1))
        with self.assertRaises(JournalError):
            reopen_period(self.company, date(2026, 3, 1), self.staff, ' ')
        reopen_period(self.company, date(2026, 3, 1), self.staff, 'Fake late bill')
        self.assertEqual(locked_through(self.company), date(2026, 2, 1))
        self.assertEqual(list(Period.objects.filter(company=self.company, locked=True).values_list('start', flat=True)),
                         [date(2026, 2, 1)])
        self.assertEqual(self.post(day=date(2026, 3, 5)).number, 2)
        events = list(PeriodEvent.objects.order_by('id').values_list('period__start', 'action', 'reason'))
        self.assertIn((date(2026, 4, 1), 'reopen', 'Fake late bill'), events)
        self.assertIn((date(2026, 3, 1), 'reopen', 'Fake late bill'), events)
        self.assertEqual(sum(1 for event in events if event[1] == 'lock'), 3)

    def test_months_are_given_by_their_first_day_and_other_companies_are_apart(self):
        for bad in (date(2026, 6, 2),):
            with self.assertRaises(JournalError):
                lock_period(self.company, bad, self.staff)
            with self.assertRaises(JournalError):
                reopen_period(self.company, bad, self.staff, 'Fake')
        with self.assertRaises(JournalError):
            reopen_period(self.company, date(2026, 6, 1), self.staff, 'Fake')  # not locked
        other = CompanyFactory()
        lock_period(other, date(2026, 6, 1), UserFactory(company=other, is_staff=True))
        self.assertIsNone(locked_through(self.company))
        self.assertEqual(self.post().number, 1)
