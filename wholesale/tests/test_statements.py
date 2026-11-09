"""G51: monthly statements — the PDF, sending once, opt-out, the log."""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.db import IntegrityError, transaction

from vat.services import UK
from wholesale import statements
from wholesale.aged_debtors import open_items
from wholesale.models import CustomerEmail, TradeCustomer
from wholesale.statement_pdf import build_statement_pdf
from wholesale.statements import (
    month_of,
    previous_month,
    run_monthly_statements,
    send_statement,
    statement_data,
)
from wholesale.tests.test_picking_api import pdf_text
from wholesale.tests.test_payments import PaymentCase

D = Decimal


def next_month_first(day):
    return (day.replace(day=1) + timedelta(days=32)).replace(day=1)


class StatementCase(PaymentCase):
    """``first`` (12.24) and ``second`` (8.24) issued today; the customer's invoice email is fake."""

    def setUp(self):
        super().setUp()
        self.start, self.end = month_of(self.today)
        self.run_day = next_month_first(self.today)  # the 1st after this month: its run covers this month
        self.send = mock.patch.object(statements.deliver_customer_email, 'apply_async',
                                      side_effect=lambda args, **kw: statements.deliver_customer_email(*args))
        self.send.start()
        self.addCleanup(self.send.stop)

    def run_statements(self, day=None):
        with self.captureOnCommitCallbacks(execute=True):
            run_monthly_statements(day or self.run_day)


class StatementTests(StatementCase):
    def test_months(self):
        self.assertEqual(month_of(date(2026, 2, 14)), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(previous_month(date(2026, 1, 1)), (date(2025, 12, 1), date(2025, 12, 31)))
        self.assertEqual(previous_month(date(2026, 11, 30)), (date(2026, 10, 1), date(2026, 10, 31)))

    def test_open_items_as_at_month_end(self):
        self.pay('15.00')
        self.assertEqual([(entry.invoice_id, still) for entry, still in open_items(self.customer, self.today)],
                         [(self.second.pk, D('5.48'))])
        self.assertEqual(open_items(self.customer, self.today - timedelta(days=1)), [])

    def test_the_pdf_has_the_month_and_what_is_open(self):
        self.pay('15.00', reference='Fake BACS 9')
        data = statement_data(self.customer, self.start, self.end)
        self.assertEqual((data['opening'], data['closing']), (D('0.00'), D('5.48')))
        self.assertEqual([still for _entry, still in data['open_items']], [D('5.48')])
        self.assertEqual(data['ageing']['total'], D('5.48'))
        text = pdf_text(build_statement_pdf(data))
        for expected in ('Statement', self.customer.account_code, 'INV-000001', 'INV-000002', 'Fake BACS 9',
                         'Opening balance', '0.00', 'Closing balance', '5.48', 'Still to pay', '90+'):
            self.assertIn(expected, text)

    def test_monthly_run_sends_once_with_the_pdf(self):
        self.run_statements()
        self.run_statements()
        [message] = mail.outbox
        self.assertEqual(message.to, ['ap@example.test'])
        self.assertIn(f'{self.start:%B %Y}', message.subject)
        [(name, content, kind)] = message.attachments
        self.assertEqual((name, kind), (f'statement-{self.customer.account_code}-{self.start:%Y-%m}.pdf',
                                        'application/pdf'))
        self.assertTrue(content.startswith(b'%PDF'))
        email = CustomerEmail.objects.get()
        self.assertEqual((email.kind, email.status, email.to, email.period_start, email.period_end, email.created_by),
                         ('statement', 'sent', 'ap@example.test', self.start, self.end, None))
        self.assertIsNotNone(email.sent_at)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomerEmail.objects.create(customer=self.customer, kind='statement', period_start=self.start,
                                         period_end=self.end)

    def test_who_gets_one(self):
        quiet = TradeCustomer.objects.create(company=self.company, account_code='Q-1', legal_name='Fake Quiet Ltd',
                                             invoice_email='quiet@example.test')
        TradeCustomer.objects.filter(pk=self.customer.pk).update(statements_by_email=False)
        self.run_statements()
        self.assertEqual(mail.outbox, [])  # opted out, and one with nothing to say
        self.assertFalse(CustomerEmail.objects.filter(customer=quiet).exists())
        TradeCustomer.objects.filter(pk=self.customer.pk).update(statements_by_email=True, status='closed')
        self.run_statements()
        self.assertEqual(mail.outbox, [])
        TradeCustomer.objects.filter(pk=self.customer.pk).update(status='on_hold')
        self.run_statements()
        self.assertEqual(len(mail.outbox), 1)

    def test_a_balance_brought_forward_still_gets_a_statement(self):
        later = next_month_first(self.run_day)  # nothing happened in the next month, but they still owe
        self.run_statements(later)
        self.assertEqual(CustomerEmail.objects.get().period_start, self.run_day)

    def test_no_address_and_a_failure_are_logged_not_retried(self):
        TradeCustomer.objects.filter(pk=self.customer.pk).update(invoice_email='')
        self.run_statements()
        self.assertEqual((CustomerEmail.objects.get().status, mail.outbox), ('no_address', []))
        self.run_statements()
        self.assertEqual(CustomerEmail.objects.count(), 1)
        TradeCustomer.objects.filter(pk=self.customer.pk).update(invoice_email='ap@example.test')
        with mock.patch('django.core.mail.EmailMessage.send', side_effect=ConnectionRefusedError('Fake')), \
                self.assertLogs('wholesale.statements', 'WARNING') as logs:
            self.run_statements(next_month_first(self.run_day))
        failed = CustomerEmail.objects.latest('id')
        self.assertEqual((failed.status, failed.error), ('failed', 'ConnectionRefusedError'))
        self.assertNotIn('ap@example.test', ''.join(logs.output))

    def test_by_hand_any_number_of_times(self):
        for _ in range(2):
            with self.captureOnCommitCallbacks(execute=True):
                email = send_statement(self.customer, self.start, self.end, user=self.staff)
            self.assertEqual(email.created_by, self.staff)
        self.assertEqual(len(mail.outbox), 2)
        self.run_statements()
        self.assertEqual(len(mail.outbox), 3)  # the schedule's own one still goes

    def test_the_run_on_the_first_takes_the_uk_month(self):
        # 23:30 UTC on 31 October in summer time is already 1 November in the UK... and the reverse in winter.
        moment = datetime(2026, 11, 1, 0, 30, tzinfo=dt_timezone.utc)
        with mock.patch('django.utils.timezone.now', return_value=moment):
            self.assertEqual(statements.uk_date(), moment.astimezone(UK).date())
        self.assertEqual(previous_month(moment.astimezone(UK).date()), (date(2026, 10, 1), date(2026, 10, 31)))
