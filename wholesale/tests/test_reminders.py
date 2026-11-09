"""G51: payment reminders — the stage table, one email per customer per day, never the same reminder twice."""

from datetime import date, timedelta
from decimal import Decimal

from unittest import mock

from django.core import mail

from wholesale import reminders
from wholesale.models import CustomerEmail, ReminderItem, TradeCustomer
from wholesale.reminders import due_stage, run_reminders
from wholesale.tests.test_statements import StatementCase

D = Decimal
DUE = date(2026, 6, 30)


class StageTests(StatementCase):
    def test_due_stage_table(self):
        for days, recorded, expected in (
            (-4, set(), None),
            (-3, set(), 'before_due'),
            (-1, set(), 'before_due'),
            (-1, {'before_due'}, None),
            (0, set(), None),  # due today: not "before" any more, not yet overdue
            (6, set(), None),
            (7, set(), 'overdue_7'),
            (12, set(), 'overdue_7'),  # caught up within 6 days
            (13, set(), 'overdue_7'),  # the last day it is caught up
            (13, {'overdue_7'}, None),
            (14, set(), 'overdue_14'),
            (14, {'overdue_7'}, 'overdue_14'),
            (20, set(), 'overdue_14'),
            (21, set(), None),  # too late to catch up: an old invoice is never chased all at once
            (100, set(), None),
            (8, {'overdue_14'}, None),  # never a stage older than one recorded
        ):
            with self.subTest(days=days, recorded=recorded):
                self.assertEqual(due_stage(DUE, DUE + timedelta(days=days), recorded), expected)


class ReminderTests(StatementCase):
    """Both invoices are due on ``self.due``."""

    def setUp(self):
        super().setUp()
        self.due = self.first.due_date

    def remind(self, days):
        with self.captureOnCommitCallbacks(execute=True):
            return run_reminders(self.due + timedelta(days=days))

    def test_one_email_lists_every_invoice_of_the_day(self):
        self.pay('2.00')
        self.remind(-3)
        [message] = mail.outbox
        self.assertEqual(message.to, ['ap@example.test'])
        self.assertIn('due', message.subject.lower())
        for text in ('INV-000001', '10.24', 'INV-000002', '8.24', '18.48', f'{self.due:%d %B %Y}'):
            self.assertIn(text, message.body)
        email = CustomerEmail.objects.get()
        self.assertEqual((email.kind, email.status, email.created_by), ('reminder', 'sent', None))
        self.assertEqual(sorted(email.reminder_items.values_list('invoice__number', 'stage', 'open_amount')),
                         [(1, 'before_due', D('10.24')), (2, 'before_due', D('8.24'))])

    def test_never_the_same_reminder_twice(self):
        for days in (-3, -3, -2, -1, 0, 7, 7, 8, 13, 14, 15, 20, 21, 40):
            self.remind(days)
        self.assertEqual([email.reminder_items.values_list('stage', flat=True).first()
                          for email in CustomerEmail.objects.order_by('id')], ['before_due', 'overdue_7', 'overdue_14'])
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(ReminderItem.objects.count(), 6)

    def test_paid_invoices_are_not_reminded(self):
        self.remind(-3)
        self.pay('12.24')  # the first, oldest
        self.remind(7)
        self.assertEqual(list(CustomerEmail.objects.latest('id').reminder_items.values_list('invoice', flat=True)),
                         [self.second.pk])
        self.pay('8.24')
        self.remind(14)
        self.assertEqual(CustomerEmail.objects.count(), 2)

    def test_opt_out_and_closed_customers(self):
        TradeCustomer.objects.filter(pk=self.customer.pk).update(reminders_by_email=False)
        self.remind(-3)
        TradeCustomer.objects.filter(pk=self.customer.pk).update(reminders_by_email=True, status='closed')
        self.remind(-2)
        self.assertFalse(CustomerEmail.objects.exists())
        TradeCustomer.objects.filter(pk=self.customer.pk).update(status='on_hold')
        self.remind(-1)  # still in its window
        self.assertEqual(CustomerEmail.objects.get().reminder_items.count(), 2)

    def test_no_address_is_logged_and_the_stage_counts_as_done(self):
        TradeCustomer.objects.filter(pk=self.customer.pk).update(invoice_email='')
        self.remind(7)
        self.assertEqual((CustomerEmail.objects.get().status, mail.outbox), ('no_address', []))
        TradeCustomer.objects.filter(pk=self.customer.pk).update(invoice_email='ap@example.test')
        self.remind(8)
        self.assertEqual(CustomerEmail.objects.count(), 1)

    def test_a_stage_recorded_meanwhile_is_dropped_not_sent_twice(self):
        # Another worker recorded the first invoice's stage between this run's read and its write.
        other = CustomerEmail.objects.create(customer=self.customer, kind='reminder', status='sent')
        ReminderItem.objects.create(email=other, invoice=self.first, stage='overdue_7', open_amount=D('12.24'))
        with mock.patch.object(reminders, '_recorded', return_value={}):  # it read before the other run wrote
            self.remind(7)
        mine = CustomerEmail.objects.exclude(pk=other.pk).get()
        self.assertEqual(list(mine.reminder_items.values_list('invoice', flat=True)), [self.second.pk])
        self.assertEqual(len(mail.outbox), 1)
