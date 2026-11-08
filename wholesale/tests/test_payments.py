"""G49: payments and allocation. The balance is always the sum of the ledger; allocation only says what is open."""

import importlib
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from wholesale import credit, payments
from wholesale.credit_notes import create_credit_note, issue_credit_note
from wholesale.invoices import create_invoice, issue
from wholesale.ledger import balance, post_adjustment
from wholesale.models import Allocation, LedgerEntry, Payment, TradeCustomer
from wholesale.payments import PaymentError, open_debits, on_account, record_payment, release, reverse_payment
from wholesale.returns import record_return
from wholesale.runs import complete_run, deliver, start_run
from wholesale.tests.test_credit_notes import CreditBase

D = Decimal


class PaymentCase(CreditBase):
    def setUp(self):
        super().setUp()
        self.first = issue(create_invoice(self.picked_order(), self.staff), self.staff)  # 12.24
        self.second = issue(create_invoice(self.picked_order(rice=1), self.staff), self.staff)  # 8.24

    def pay(self, amount, allocate='oldest', **fields):
        return record_payment(self.customer, self.staff, amount=D(amount), method=fields.pop('method', 'bank_transfer'),
                              received_on=fields.pop('received_on', self.today), allocate=allocate, **fields)

    def entry(self, invoice):
        return LedgerEntry.objects.get(invoice=invoice)

    def open_amounts(self):
        return {(row.invoice.number_display if row.invoice_id else row.description): row.open
                for row in open_debits(self.customer)}

    def assert_balance(self, expected):
        entries = LedgerEntry.objects.filter(customer=self.customer)
        self.assertEqual(balance(self.customer), sum(entry.amount for entry in entries))
        self.assertEqual(balance(self.customer), D(expected))


class AllocationTests(PaymentCase):
    def test_oldest_first(self):
        payment = self.pay('15.00', reference='Fake BACS 1')
        entry = payment.ledger_entries.get(kind='payment')
        self.assertEqual((entry.amount, entry.entry_date, entry.payment_ref), (D('-15.00'), self.today, 'Fake BACS 1'))
        self.assertEqual(self.open_amounts(), {'INV-000002': D('5.48')})
        self.assertEqual(list(on_account(self.customer)), [])
        self.assert_balance('5.48')

    def test_manual(self):
        self.pay('5.00', allocate=[{'debit': self.entry(self.second).pk, 'amount': D('5.00')}])
        self.assertEqual(self.open_amounts(), {'INV-000001': D('12.24'), 'INV-000002': D('3.24')})
        self.assert_balance('15.48')

    def test_an_overpayment_stays_on_account(self):
        self.pay('30.00')
        self.assertEqual(self.open_amounts(), {})
        self.assertEqual([(row.amount, row.remaining) for row in on_account(self.customer)],
                         [(D('-30.00'), D('9.52'))])
        self.assert_balance('-9.52')

    def test_nothing_is_allocated_beyond_what_is_open_or_paid(self):
        other = TradeCustomer.objects.create(company=self.company, account_code='C-2', legal_name='Fake Cafe Ltd')
        theirs = post_adjustment(other, D('3.00'), 'Fake', self.staff)
        for targets in ([{'debit': self.entry(self.second).pk, 'amount': D('9.00')}],  # 8.24 open
                        [{'debit': self.entry(self.first).pk, 'amount': D('4.00')},
                         {'debit': self.entry(self.second).pk, 'amount': D('2.00')}],  # 6.00 of a 5.00 payment
                        [{'debit': theirs.pk, 'amount': D('1.00')}],
                        [{'debit': self.entry(self.first).pk, 'amount': D('0.001')}],
                        [{'debit': self.entry(self.first).pk, 'amount': D('1.00')},
                         {'debit': self.entry(self.first).pk, 'amount': D('1.00')}]):
            with self.subTest(targets=targets), self.assertRaises(PaymentError):
                self.pay('5.00', allocate=targets)
        self.assertFalse(Payment.objects.exists())
        self.assert_balance('20.48')

    def test_release_puts_it_back_on_account_and_it_can_go_elsewhere(self):
        payment = self.pay('12.24')
        allocation = Allocation.objects.get(credit__payment=payment)
        release(allocation, self.staff)
        allocation.refresh_from_db()
        self.assertEqual(allocation.released_by, self.staff)
        self.assertEqual(self.open_amounts(), {'INV-000001': D('12.24'), 'INV-000002': D('8.24')})
        credit_entry = payment.ledger_entries.get()
        payments.allocate(credit_entry, self.staff, [{'debit': self.entry(self.second).pk, 'amount': D('8.24')}])
        self.assertEqual(self.open_amounts(), {'INV-000001': D('12.24')})
        self.assertEqual([row.remaining for row in on_account(self.customer)], [D('4.00')])
        with self.assertRaises(PaymentError):
            release(allocation, self.staff)  # once
        self.assert_balance('8.24')

    def test_a_reversal_cancels_the_payment_and_its_allocations(self):
        payment = self.pay('15.00', method='cheque', reference='Fake 000123')
        reversed_payment = reverse_payment(payment, self.staff, 'Fake: cheque bounced')
        self.assertEqual((reversed_payment.reversed_by, reversed_payment.reversal_reason),
                         (self.staff, 'Fake: cheque bounced'))
        self.assertEqual(sorted(payment.ledger_entries.values_list('kind', 'amount')),
                         [('payment', D('-15.00')), ('payment_reversal', D('15.00'))])
        self.assertEqual(self.open_amounts(), {'INV-000001': D('12.24'), 'INV-000002': D('8.24')})
        self.assertEqual(list(on_account(self.customer)), [])
        with self.assertRaises(PaymentError):
            reverse_payment(payment, self.staff, 'Fake')
        self.assert_balance('20.48')

    def test_a_reversal_after_releasing_by_hand_is_clean(self):
        payment = self.pay('5.00')
        for allocation in Allocation.objects.filter(credit__payment=payment):
            release(allocation, self.staff)
        reverse_payment(payment, self.staff, 'Fake: keyed twice')
        self.assertEqual(list(on_account(self.customer)), [])
        self.assert_balance('20.48')

    def test_a_credit_note_pays_its_own_invoice_and_the_rest_goes_on_account(self):
        self.pay('8.24', allocate=[{'debit': self.entry(self.second).pk, 'amount': D('8.24')}])  # 2nd paid
        order = self.second.order
        run = self.plan(order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        complete_run(run, self.staff)
        returned = record_return(order, self.staff, [
            {'line': self.line(order, self.rice).pk, 'reason': 'not_wanted', 'quantity': 1}])
        note = issue_credit_note(create_credit_note(returned, self.staff), self.staff)  # 2.00 against a paid one
        self.assertEqual([row.remaining for row in on_account(self.customer)], [D('2.00')])
        self.assertEqual(self.open_amounts(), {'INV-000001': D('12.24')})
        self.assert_balance('10.24')
        self.assertEqual(note.ledger_entry.allocations_out.count(), 0)

    def test_a_credit_note_on_an_open_invoice_is_allocated_to_it(self):
        order = self.first.order
        run = self.plan(order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        complete_run(run, self.staff)
        returned = record_return(order, self.staff, [
            {'line': self.line(order, self.rice).pk, 'reason': 'not_wanted', 'quantity': 1}])
        note = issue_credit_note(create_credit_note(returned, self.staff), self.staff)
        allocation = note.ledger_entry.allocations_out.get()
        self.assertEqual((allocation.debit_id, allocation.amount), (self.entry(self.first).pk, D('2.00')))
        self.assertEqual(self.open_amounts(), {'INV-000001': D('10.24'), 'INV-000002': D('8.24')})

    def test_overdue_now_reads_allocations(self):
        # Both invoices are due 30 days on; paying the second by hand leaves only the first overdue.
        self.pay('8.24', allocate=[{'debit': self.entry(self.second).pk, 'amount': D('8.24')}])
        with mock.patch.object(credit, 'uk_date', return_value=self.today + timedelta(days=40)):
            late = credit.status(self.customer)['overdue']
        self.assertEqual([(row['number'], row['open']) for row in late], [('INV-000001', '12.24')])

    def test_payment_rules_and_a_repeat(self):
        for fields in ({'amount': D('0')}, {'amount': D('1.005')}, {'amount': D('10000000.00')},
                       {'method': 'bitcoin'}, {'received_on': self.today + timedelta(days=1)},
                       {'reference': 'x' * 61}):
            body = {'amount': D('1.00'), 'method': 'cash', 'received_on': self.today, **fields}
            with self.subTest(fields=fields), self.assertRaises(PaymentError):
                record_payment(self.customer, self.staff, **body)
        member = type(self.staff).objects.create_user(email='member@example.test', password=None,
                                                      company=self.company)
        with self.assertRaises(PaymentError):
            record_payment(self.customer, member, amount=D('1.00'), method='cash',
                           received_on=self.today)
        ref = uuid.uuid4()
        first = self.pay('2.00', client_ref=ref)
        again = self.pay('2.00', client_ref=ref)
        self.assertEqual((again.pk, again.repeated), (first.pk, True))
        with self.assertRaises(PaymentError):
            self.pay('3.00', client_ref=ref)
        self.assert_balance('18.48')

    def test_history_is_kept(self):
        payment = self.pay('5.00')
        allocation = Allocation.objects.get(credit__payment=payment)
        allocation.amount = D('1.00')
        with self.assertRaises(ValidationError):
            allocation.save()
        for attempt in (allocation.delete, lambda: Allocation.objects.filter(pk=allocation.pk).delete(),
                        lambda: Allocation.objects.filter(pk=allocation.pk).update(amount=D('1.00')),
                        payment.delete, lambda: Payment.objects.filter(pk=payment.pk).update(amount=D('1.00'))):
            with self.assertRaises(ValidationError), transaction.atomic():
                attempt()
        with self.assertRaises(IntegrityError), transaction.atomic():
            LedgerEntry.objects.create(customer=self.customer, kind='payment', entry_date=self.today,
                                       amount=D('-1.00'), description='Fake')  # a payment entry needs its payment


class BackfillTests(PaymentCase):
    migration = importlib.import_module('wholesale.migrations.0015_allocate_credit_notes')

    def test_issued_credit_notes_are_allocated_once_and_back(self):
        order = self.first.order
        run = self.plan(order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        complete_run(run, self.staff)
        returned = record_return(order, self.staff, [
            {'line': self.line(order, self.rice).pk, 'reason': 'not_wanted', 'quantity': 1}])
        note = issue_credit_note(create_credit_note(returned, self.staff), self.staff)
        Allocation.objects.all()._raw_delete('default')  # as before G49
        self.migration.allocate(apps, None)
        self.migration.allocate(apps, None)
        self.assertEqual(list(Allocation.objects.values_list('credit__credit_note', 'debit__invoice', 'amount')),
                         [(note.pk, self.first.pk, D('2.00'))])
        self.migration.unallocate(apps, None)
        self.assertFalse(Allocation.objects.exists())
