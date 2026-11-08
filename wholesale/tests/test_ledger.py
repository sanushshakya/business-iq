"""G47: the customer ledger — append-only entries; the balance is always their sum."""

import importlib
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError

from wholesale.credit_notes import create_credit_note, issue_credit_note
from wholesale.invoices import InvoiceError, create_invoice, issue
from wholesale import ledger
from wholesale.ledger import LedgerError, balance, post_adjustment, statement
from wholesale.models import LedgerEntry
from wholesale.returns import record_return
from wholesale.tests.test_credit_notes import CreditBase
from wholesale.runs import complete_run, deliver, start_run

D = Decimal


class LedgerTests(CreditBase):
    def setUp(self):
        super().setUp()
        self.order = self.picked_order(rice=3, chops=2, chops_kg=D('1.040'))
        self.invoice = issue(create_invoice(self.order, self.staff), self.staff)  # 6.00 + 6.24 = 12.24

    def test_issuing_an_invoice_posts_its_gross_once(self):
        entry = LedgerEntry.objects.get(invoice=self.invoice)
        self.assertEqual((entry.kind, entry.amount, entry.entry_date, entry.customer_id),
                         ('invoice', D('12.24'), self.invoice.invoice_date, self.customer.pk))
        self.assertEqual(entry.description, 'Invoice INV-000001')
        with self.assertRaises(InvoiceError):
            issue(self.invoice, self.staff)
        self.assertEqual(LedgerEntry.objects.filter(invoice=self.invoice).count(), 1)
        self.assertEqual(balance(self.customer), D('12.24'))

    def test_a_credit_note_and_adjustments_take_away_and_the_balance_is_the_sum(self):
        run = self.plan(self.order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        complete_run(run, self.staff)
        returned = record_return(self.order, self.staff, [
            {'line': self.line(self.order, self.rice).pk, 'reason': 'not_wanted', 'quantity': 1}])
        note = issue_credit_note(create_credit_note(returned, self.staff), self.staff)
        entry = LedgerEntry.objects.get(credit_note=note)
        self.assertEqual((entry.kind, entry.amount, entry.description), ('credit_note', D('-2.00'),
                                                                         'Credit note CN-000001'))
        post_adjustment(self.customer, D('-0.24'), 'Fake rounding write-off', self.staff)
        post_adjustment(self.customer, D('5.00'), 'Fake delivery charge', self.staff)
        entries = LedgerEntry.objects.filter(customer=self.customer)
        self.assertEqual(balance(self.customer), sum(e.amount for e in entries))
        self.assertEqual(balance(self.customer), D('15.00'))  # 12.24 - 2.00 - 0.24 + 5.00

    def test_a_free_invoice_posts_nothing(self):
        from inventory.models import Product

        Product.objects.filter(pk__in=[self.rice.pk, self.chops.pk]).update(wholesale_price=D('0'))
        free = issue(create_invoice(self.picked_order(rice=1, chops=2, chops_kg=D('1.000')), self.staff), self.staff)
        self.assertEqual(free.gross_total, D('0.00'))
        self.assertFalse(LedgerEntry.objects.filter(invoice=free).exists())

    def test_a_customer_with_no_entries_owes_nothing(self):
        from wholesale.models import TradeCustomer

        other = TradeCustomer.objects.create(company=self.company, account_code='C-2', legal_name='Fake Cafe Ltd')
        self.assertEqual(balance(other), D('0.00'))
        self.assertEqual(statement(other, self.today, self.today)['closing'], D('0.00'))

    def test_balance_on_a_date_and_the_statement_add_up(self):
        first = LedgerEntry.objects.get(invoice=self.invoice)
        early = post_adjustment(self.customer, D('3.00'), 'Fake opening balance', self.staff)
        # Dates are set at posting; back-date one for this test with raw SQL (no model path can).
        with connection.cursor() as cursor:
            cursor.execute(f'UPDATE {LedgerEntry._meta.db_table} SET entry_date = %s WHERE id = %s',
                           [self.today - timedelta(days=10), early.pk])
        post_adjustment(self.customer, D('-1.50'), 'Fake goodwill', self.staff)
        self.assertEqual(balance(self.customer, on=self.today - timedelta(days=1)), D('3.00'))
        self.assertEqual(balance(self.customer), D('13.74'))
        report = statement(self.customer, self.today, self.today)
        self.assertEqual(report['opening'], D('3.00'))
        self.assertEqual([row.amount for row in report['entries']], [first.amount, D('-1.50')])
        self.assertEqual([row.running for row in report['entries']], [D('15.24'), D('13.74')])
        self.assertEqual(report['closing'], report['opening'] + sum(row.amount for row in report['entries']))
        self.assertEqual(report['closing'], balance(self.customer))

    def test_adjustment_rules(self):
        for amount, reason in [(D('0'), 'Fake'), (D('1.005'), 'Fake'), (D('1000000.00'), 'Fake'),
                               (D('1.00'), '   '), (1, 'Fake'), (D('NaN'), 'Fake')]:
            with self.subTest(amount=amount, reason=reason), self.assertRaises(LedgerError):
                post_adjustment(self.customer, amount, reason, self.staff)
        member = type(self.staff).objects.create_user(email='member@example.test', password=None,
                                                      company=self.company)
        with self.assertRaises(LedgerError):
            post_adjustment(self.customer, D('1.00'), 'Fake', member)
        entry = post_adjustment(self.customer, D('-999999.99'), '  Fake write-off  ', self.staff)
        self.assertEqual((entry.reason, entry.created_by, entry.entry_date), ('Fake write-off', self.staff,
                                                                             self.today))

    def test_entries_cannot_be_edited_or_deleted(self):
        entry = LedgerEntry.objects.get(invoice=self.invoice)
        entry.amount = D('1.00')
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()
        for attempt in (lambda: LedgerEntry.objects.filter(pk=entry.pk).update(amount=D('1.00')),
                        lambda: LedgerEntry.objects.filter(pk=entry.pk).delete(),
                        lambda: LedgerEntry.objects.bulk_update([entry], ['amount']),
                        lambda: LedgerEntry._base_manager.filter(pk=entry.pk).update(amount=D('1.00')),
                        lambda: LedgerEntry.objects.bulk_create(
                            [LedgerEntry(customer=self.customer, kind='invoice', entry_date=self.today,
                                         amount=D('1.00'), invoice=self.invoice, description='Fake')],
                            update_conflicts=True, unique_fields=['invoice'], update_fields=['amount'])):
            with self.assertRaises(ValidationError), transaction.atomic():
                attempt()
        self.assertEqual(LedgerEntry.objects.get(pk=entry.pk).amount, D('12.24'))

    def test_deleting_what_an_entry_points_at_is_refused(self):
        post_adjustment(self.customer, D('1.00'), 'Fake', self.staff)
        for victim, error in ((self.invoice, ValidationError), (self.customer, ProtectedError),
                              (self.staff, ProtectedError)):
            with self.subTest(victim=type(victim).__name__), self.assertRaises(error), transaction.atomic():
                victim.delete()
        self.assertEqual(LedgerEntry.objects.filter(customer=self.customer).count(), 2)

    def test_a_repeated_client_ref_posts_once(self):
        import uuid

        ref = uuid.uuid4()
        first = post_adjustment(self.customer, D('2.00'), 'Fake', self.staff, client_ref=ref)
        again = post_adjustment(self.customer, D('2.00'), 'Fake', self.staff, client_ref=ref)
        self.assertEqual((again.pk, again.repeated), (first.pk, True))
        with self.assertRaises(ledger.AdjustmentConflict):
            post_adjustment(self.customer, D('3.00'), 'Fake', self.staff, client_ref=ref)
        self.assertEqual(balance(self.customer), D('14.24'))

    def test_the_database_refuses_a_wrong_sign_or_a_second_entry_for_a_document(self):
        for fields in ({'kind': 'invoice', 'amount': D('-1.00'), 'invoice': None},
                       {'kind': 'credit_note', 'amount': D('1.00')},
                       {'kind': 'adjustment', 'amount': D('1.00'), 'reason': ''},
                       {'kind': 'adjustment', 'amount': D('0.00'), 'reason': 'Fake'},
                       {'kind': 'invoice', 'amount': D('1.00'), 'invoice': self.invoice}):
            with self.subTest(fields=fields), self.assertRaises(IntegrityError), transaction.atomic():
                LedgerEntry.objects.create(customer=self.customer, entry_date=self.today, description='Fake',
                                           **fields)


class BackfillTests(CreditBase):
    migration = importlib.import_module('wholesale.migrations.0011_backfill_ledger')

    def test_backfill_posts_issued_documents_once_and_reverses(self):
        order = self.picked_order(rice=2, chops=2, chops_kg=D('1.000'))
        invoice = issue(create_invoice(order, self.staff), self.staff)  # 4.00 + 6.00
        draft = create_invoice(self.picked_order(rice=1, chops=2, chops_kg=D('1.000')), self.staff)
        run = self.plan(order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        complete_run(run, self.staff)
        returned = record_return(order, self.staff, [
            {'line': self.line(order, self.rice).pk, 'reason': 'not_wanted', 'quantity': 1}])
        note = issue_credit_note(create_credit_note(returned, self.staff), self.staff)
        LedgerEntry.objects.all()._raw_delete('default')  # as before G47
        self.migration.backfill(apps, None)
        self.migration.backfill(apps, None)  # twice: no duplicates
        self.assertEqual(sorted(LedgerEntry.objects.values_list('kind', 'amount', 'entry_date', 'description')),
                         [('credit_note', D('-2.00'), note.credit_date, 'Credit note CN-000001'),
                          ('invoice', D('10.00'), invoice.invoice_date, 'Invoice INV-000001')])
        self.assertFalse(LedgerEntry.objects.filter(invoice=draft).exists())
        self.migration.unpost(apps, None)
        self.assertFalse(LedgerEntry.objects.exists())
