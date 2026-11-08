"""G45: credit notes — against the issued invoice's lines, never more than invoiced, VAT at the invoice rates."""

from decimal import Decimal
from unittest import mock

from django.core import mail
from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models import Sum

from common.tests.factories import CompanyFactory, ProductFactory
from inventory.ledger import dispatch_location, relocate
from inventory.models import StockBatch
from wholesale import credit_notes, invoices
from wholesale.credit_notes import CreditNoteError, create_credit_note, issue_credit_note
from wholesale.invoice_pdf import build_credit_note_pdf
from wholesale.invoices import create_invoice, issue
from wholesale.models import (
    CreditNote,
    CreditNoteLine,
    ReturnLine,
    SalesOrderLine,
    SalesOrderPick,
    TradeCustomer,
)
from wholesale.returns import record_return
from wholesale.runs import complete_run, deliver, start_run
from wholesale.tests.test_picking_api import pdf_text
from wholesale.tests.test_runs import Base

D = Decimal


class CreditBase(Base):
    def setUp(self):
        super().setUp()
        TradeCustomer.objects.filter(pk=self.customer.pk).update(vat_number='GB987654321',
                                                                 invoice_email='ap@example.test')
        self.cola = ProductFactory(company=self.company, name='Fake Cola', stock_quantity=0,
                                   wholesale_price=D('0.4150'), vat_code='standard')
        self.stock(self.cola, 20, None, 'COLA-1')
        self.queue = mock.patch.object(invoices.send_invoice_email, 'apply_async')
        self.queue.start()
        self.addCleanup(self.queue.stop)
        self.cn_queue = mock.patch.object(credit_notes.send_credit_note_email, 'apply_async')
        self.cn_queue.start()
        self.addCleanup(self.cn_queue.stop)

    def line(self, order, product):
        return SalesOrderLine.objects.get(order=order, product=product, substitute_for__isnull=True)


class CreditNoteTests(CreditBase):
    def setUp(self):
        super().setUp()
        self.order = self.picked_order(rice=3, chops=2, chops_kg=D('1.040'))
        # A 20% line, priced and picked as confirm and picking would have (straight into DISPATCH).
        cola_line = SalesOrderLine.objects.create(
            order=self.order, product=self.cola, quantity=7, unit_price=D('0.4150'), price_source='base',
            vat_code='standard', vat_rate=D('20.00'), picked_quantity=7)
        warehouse_lot = StockBatch.objects.get(location=self.warehouse, batch_number='COLA-1')
        SalesOrderPick.objects.create(line=cola_line, batch=warehouse_lot, quantity=7)
        with transaction.atomic():
            relocate(StockBatch.objects.select_for_update().get(pk=warehouse_lot.pk), quantity=7, weight_kg=None,
                     to=dispatch_location(self.company))
        self.invoice = issue(create_invoice(self.order, self.staff), self.staff)
        run = self.plan(self.order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        complete_run(run, self.staff)
        self.order.refresh_from_db()

    def give_back(self, **lines):
        requested = []
        for product, amounts in lines.items():
            requested.append({'line': self.line(self.order, getattr(self, product)).pk, 'reason': 'not_wanted',
                              **amounts})
        return record_return(self.order, self.staff, requested)

    def test_vat_at_the_invoice_rates_and_the_last_credit_takes_exactly_the_rest(self):
        first = create_credit_note(self.give_back(cola={'quantity': 2}, rice={'quantity': 1}), self.staff)
        rows = {row.description: (row.quantity, row.net, row.vat) for row in first.lines.all()}
        # 2 x 0.4150 = 0.83; VAT 20% = 0.166 -> 0.17. Rice 1 x 2.0000 = 2.00, zero-rated.
        self.assertEqual(rows, {'Fake Cola': (2, D('0.83'), D('0.17')), 'Fake Rice': (1, D('2.00'), D('0.00'))})
        self.assertEqual((first.net_total, first.vat_total, first.gross_total), (D('2.83'), D('0.17'), D('3.00')))
        self.assertEqual({row['rate']: (row['net'], row['vat']) for row in first.vat_summary},
                         {'0.00': ('2.00', '0.00'), '20.00': ('0.83', '0.17')})
        issue_credit_note(first, self.staff)
        rest = create_credit_note(self.give_back(cola={'quantity': 5}), self.staff)
        # The invoice line is 2.91 net, 0.58 VAT; the rest is 2.08 and 0.41 (not 5 x 0.415 = 2.075 -> 2.08, 0.42).
        self.assertEqual((rest.lines.get().net, rest.lines.get().vat), (D('2.08'), D('0.41')))
        issue_credit_note(rest, self.staff)
        cola_invoiced = self.invoice.lines.get(product=self.cola)
        credited = CreditNoteLine.objects.filter(invoice_line=cola_invoiced).aggregate(
            net=Sum('net'), vat=Sum('vat'), units=Sum('quantity'))
        self.assertEqual((credited['net'], credited['vat'], credited['units']),
                         (cola_invoiced.net, cola_invoiced.vat, cola_invoiced.quantity))

    def test_catch_weight_is_credited_on_kg(self):
        note = create_credit_note(self.give_back(chops={'quantity': 2, 'weight_kg': D('1.040')}), self.staff)
        row = note.lines.get()
        self.assertEqual((row.quantity, row.weight_kg, row.net), (2, D('1.040'), D('6.24')))  # 1.040 x 6.0000

    def test_never_more_than_invoiced(self):
        returned = self.give_back(rice={'quantity': 3})
        issue_credit_note(create_credit_note(returned, self.staff), self.staff)
        # A return line beyond what was invoiced (written around the return checks) is refused on credit.
        extra = self.give_back(cola={'quantity': 1})
        ReturnLine.objects.create(customer_return=extra, order_line=self.line(self.order, self.rice),
                                  batch_number='RICE-1', quantity=1, reason='not_wanted', disposition='waste')
        with self.assertRaises(CreditNoteError):
            create_credit_note(extra, self.staff)
        self.assertFalse(CreditNote.objects.filter(customer_return=extra).exists())

    def test_the_cap_is_checked_again_at_issue(self):
        first = create_credit_note(self.give_back(rice={'quantity': 2}), self.staff)
        second = create_credit_note(self.give_back(rice={'quantity': 1}), self.staff)
        ReturnLine.objects.filter(customer_return=second.customer_return).update(quantity=2)
        second.lines.update(quantity=2)  # a draft drawn up against stale figures
        issue_credit_note(first, self.staff)
        with self.assertRaises(CreditNoteError):
            issue_credit_note(second, self.staff)
        second.refresh_from_db()
        self.assertEqual((second.status, second.number), ('draft', None))

    def test_one_credit_note_per_return_and_only_against_an_issued_invoice(self):
        returned = self.give_back(rice={'quantity': 1})
        create_credit_note(returned, self.staff)
        with self.assertRaises(CreditNoteError):
            create_credit_note(returned, self.staff)

    def test_numbering_immutability_pdf_and_email(self):
        first = issue_credit_note(create_credit_note(self.give_back(rice={'quantity': 1}), self.staff), self.staff)
        discarded = create_credit_note(self.give_back(rice={'quantity': 1}), self.staff)
        credit_notes.discard_draft(discarded)
        second = issue_credit_note(create_credit_note(self.give_back(rice={'quantity': 1}), self.staff),
                                   self.staff)
        self.assertEqual([first.number_display, second.number_display], ['CN-000001', 'CN-000002'])
        other = CompanyFactory()
        self.assertFalse(CreditNote.objects.filter(company=other).exists())

        first.net_total = D('0.01')
        with self.assertRaises(ValidationError):
            first.save()
        with self.assertRaises(ValidationError):
            CreditNote.objects.filter(pk=first.pk).update(net_total=D('0.01'))
        with self.assertRaises(ValidationError):
            CreditNote.objects.get(pk=first.pk).delete()
        with self.assertRaises(ValidationError):
            CreditNoteLine.objects.filter(credit_note=first).update(net=D('0.01'))

        text = pdf_text(build_credit_note_pdf(CreditNote.objects.get(pk=first.pk)))
        for expected in ('Credit note CN-000001', self.invoice.number_display, 'GB123456789', 'GB987654321', '2.00'):
            self.assertIn(expected, text)

        credit_notes.send_credit_note_email(first.pk)
        first.refresh_from_db()
        self.assertEqual((first.email_status, first.emailed_to), ('sent', 'ap@example.test'))
        [message] = mail.outbox
        self.assertIn('CN-000001', message.subject)
        self.assertEqual(message.attachments[0][0], 'CN-000001.pdf')


class DoorRefusalCreditTests(CreditBase):
    def test_a_refusal_after_the_invoice_is_credited_and_one_before_is_never_billed(self):
        before = self.picked_order(rice=3, chops=1, chops_kg=D('0.520'))
        after = self.picked_order(rice=3, chops=1, chops_kg=D('0.520'))
        invoice_after = issue(create_invoice(after, self.staff), self.staff)  # issued before the delivery
        run = self.plan(before, after)
        start_run(run, self.staff)
        for order in (before, after):
            deliver(run.stops.get(order=order), self.driver, received_by='Fake Chef', refused=[
                {'line': self.line(order, self.rice).pk, 'quantity': 1, 'reason': 'damaged'}])
        complete_run(run, self.staff)
        refused_after = after.returns.get()
        note = create_credit_note(refused_after, self.staff)
        self.assertEqual((note.invoice, note.lines.get().quantity), (invoice_after, 1))
        invoice_before = issue(create_invoice(before, self.staff), self.staff)
        self.assertEqual(invoice_before.lines.get(product=self.rice).quantity, 2)  # billed only what was delivered
        with self.assertRaises(CreditNoteError):
            create_credit_note(before.returns.get(), self.staff)  # never invoiced, so nothing to credit
