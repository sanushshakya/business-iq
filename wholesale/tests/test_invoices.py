"""G43: invoices — billed on what was picked, VAT per line, gap-free numbers, frozen once issued, emailed.
All names, addresses and emails are fake."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase

from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from inventory.models import Product
from tenants.models import Company
from wholesale import invoices
from wholesale.invoices import InvoiceError, create_invoice, issue, refresh_draft
from wholesale.models import (
    Invoice,
    InvoiceLine,
    SalesOrder,
    SalesOrderLine,
    TradeCustomer,
    TradeCustomerContact,
)

D = Decimal
ISSUED_ON = date(2026, 11, 9)


def picked_line(order, product, *, unit_price, rate, vat_code='zero', quantity=None, weight_kg=None,
                picked_quantity=None, picked_weight_kg=None, **fields):
    return SalesOrderLine.objects.create(
        order=order, product=product, quantity=quantity, weight_kg=weight_kg, unit_price=D(unit_price),
        price_source='base', vat_code=vat_code, vat_rate=D(rate), picked_quantity=picked_quantity,
        picked_weight_kg=picked_weight_kg, **fields)


class Base(TestCase):
    def setUp(self):
        self.company = CompanyFactory(name='Fake Foods Ltd', address='1 Fake Wharf, Testport, TT1 1AA')
        Company.objects.filter(pk=self.company.pk).update(vat_number='GB123456789')
        self.company.refresh_from_db()
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.customer = TradeCustomer.objects.create(
            company=self.company, account_code='C-1', legal_name='E2E Fake Bistro Ltd', vat_number='GB987654321',
            invoice_email='accounts@example.test', payment_terms_days=14)
        self.rice = ProductFactory(company=self.company, name='Fake Rice')
        self.cola = ProductFactory(company=self.company, name='Fake Cola', vat_code='standard')
        self.lamb = ProductFactory(company=self.company, name='Fake Lamb', sold_by=Product.WEIGHT,
                                   price_per_kg=D('12.99'))
        self.chops = ProductFactory(company=self.company, name='Fake Chops', sold_by=Product.WEIGHT,
                                    is_catch_weight=True, price_per_kg=D('9.99'), nominal_weight_kg=D('0.500'))
        self.numbers = 0
        uk = mock.patch('wholesale.invoices.uk_date', return_value=ISSUED_ON)
        uk.start()
        self.addCleanup(uk.stop)

    def order(self, company=None, customer=None, status=SalesOrder.PICKED):
        self.numbers += 1
        return SalesOrder.objects.create(
            company=company or self.company, number=self.numbers, customer=customer or self.customer,
            delivery_date=date(2026, 11, 8), customer_ref='PO-77', line1='1 Fake Street', town='Testtown',
            postcode='TT1 1TT', status=status, created_by=self.staff)

    def mixed_order(self):
        order = self.order()
        # 3 x 1.0050 = 3.015 -> 3.02; zero-rated.
        picked_line(order, self.rice, unit_price='1.0050', rate='0.00', quantity=3, picked_quantity=3)
        # 7 x 0.4150 = 2.905 -> 2.91; VAT 20% = 0.582 -> 0.58.
        picked_line(order, self.cola, unit_price='0.4150', rate='20.00', vat_code='standard', quantity=8,
                    picked_quantity=7)
        # 50 kg ordered, 51.2 kg picked, 9.0000/kg = 460.80.
        picked_line(order, self.lamb, unit_price='9.0000', rate='0.00', weight_kg=D('50.000'),
                    picked_weight_kg=D('51.200'))
        # Catch-weight: 3 pieces, 1.537 kg at 6.0000/kg = 9.222 -> 9.22.
        picked_line(order, self.chops, unit_price='6.0000', rate='0.00', quantity=3, weight_kg=D('1.500'),
                    picked_quantity=3, picked_weight_kg=D('1.537'))
        return order

    def issued(self, order=None):
        invoice = create_invoice(order or self.mixed_order(), self.staff)
        with mock.patch.object(invoices.send_invoice_email, 'apply_async'):
            return issue(invoice, self.staff)


class VatTests(Base):
    def test_lines_and_totals(self):
        invoice = create_invoice(self.mixed_order(), self.staff)
        lines = list(invoice.lines.order_by('id').values_list('description', 'quantity', 'weight_kg', 'unit_price',
                                                             'vat_rate', 'net', 'vat', 'gross'))
        self.assertEqual(lines, [
            ('Fake Rice', 3, None, D('1.0050'), D('0.00'), D('3.02'), D('0.00'), D('3.02')),
            ('Fake Cola', 7, None, D('0.4150'), D('20.00'), D('2.91'), D('0.58'), D('3.49')),
            ('Fake Lamb', None, D('51.200'), D('9.0000'), D('0.00'), D('460.80'), D('0.00'), D('460.80')),
            ('Fake Chops', 3, D('1.537'), D('6.0000'), D('0.00'), D('9.22'), D('0.00'), D('9.22')),
        ])
        self.assertEqual((invoice.net_total, invoice.vat_total, invoice.gross_total),
                         (D('475.95'), D('0.58'), D('476.53')))
        summary = {row['rate']: (row['net'], row['vat']) for row in invoice.vat_summary}
        self.assertEqual(summary, {'0.00': ('473.04', '0.00'), '20.00': ('2.91', '0.58')})
        self.assertEqual(sum(D(net) for net, _ in summary.values()), invoice.net_total)

    def test_what_is_billed(self):
        order = self.order()
        original = picked_line(order, self.rice, unit_price='1.0000', rate='0.00', quantity=5, picked_quantity=0)
        picked_line(order, self.cola, unit_price='0.5000', rate='20.00', quantity=5, picked_quantity=5,
                    substitute_for=original, substitution='approved')
        picked_line(order, self.lamb, unit_price='9.0000', rate='0.00', weight_kg=D('1'), substitute_for=original,
                    substitution='rejected')
        invoice = create_invoice(order, self.staff)
        self.assertEqual(list(invoice.lines.values_list('description', flat=True)), ['Fake Cola'])

    def test_nothing_picked_is_no_invoice(self):
        order = self.order()
        picked_line(order, self.rice, unit_price='1.0000', rate='0.00', quantity=5, picked_quantity=0)
        with self.assertRaises(InvoiceError):
            create_invoice(order, self.staff)


class StatusTests(Base):
    def test_only_picked_dispatched_or_delivered_orders_and_once(self):
        for status in ('draft', 'confirmed', 'picking', 'cancelled'):
            with self.subTest(status=status), self.assertRaises(InvoiceError):
                create_invoice(self.order(status=status), self.staff)
        order = self.mixed_order()
        create_invoice(order, self.staff)
        with self.assertRaises(InvoiceError):
            create_invoice(order, self.staff)

    def test_issuing_a_delivered_orders_invoice_marks_it_invoiced(self):
        picked = self.mixed_order()
        self.issued(picked)
        picked.refresh_from_db()
        self.assertEqual(picked.status, 'picked')
        delivered = self.mixed_order()
        SalesOrder.objects.filter(pk=delivered.pk).update(status='delivered')
        self.issued(delivered)
        delivered.refresh_from_db()
        self.assertEqual(delivered.status, 'invoiced')

    def test_refresh_rebuilds_a_draft_only(self):
        order = self.mixed_order()
        invoice = create_invoice(order, self.staff)
        order.lines.filter(product=self.cola).update(picked_quantity=8)
        refresh_draft(invoice)
        self.assertEqual(invoice.lines.get(product=self.cola).quantity, 8)
        with mock.patch.object(invoices.send_invoice_email, 'apply_async'):
            issue(invoice, self.staff)
        with self.assertRaises(InvoiceError):
            refresh_draft(invoice)


class NumberingTests(Base):
    def test_gap_free_per_company(self):
        first = self.issued()
        discarded = create_invoice(self.mixed_order(), self.staff)  # a draft: no number
        self.assertIsNone(discarded.number)
        discarded.delete()
        second = self.issued()
        Company.objects.filter(pk=self.company.pk).update(vat_number='')
        failed = create_invoice(self.mixed_order(), self.staff)
        with self.assertRaises(InvoiceError):
            issue(failed, self.staff)  # no VAT number of ours: no number taken
        failed.refresh_from_db()
        self.assertIsNone(failed.number)
        Company.objects.filter(pk=self.company.pk).update(vat_number='GB123456789')
        third = self.issued()
        self.assertEqual([first.number, second.number, third.number], [1, 2, 3])
        self.assertEqual(third.number_display, 'INV-000003')

        other = CompanyFactory(vat_number='GB111111111')
        customer = TradeCustomer.objects.create(company=other, account_code='C-1', legal_name='E2E Fake Other')
        order = self.order(company=other, customer=customer)
        picked_line(order, ProductFactory(company=other), unit_price='1', rate='0.00', quantity=1,
                    picked_quantity=1)
        self.assertEqual(self.issued(order).number, 1)

    def test_issued_once(self):
        invoice = self.issued()
        with self.assertRaises(InvoiceError):
            issue(invoice, self.staff)


class ImmutabilityTests(Base):
    def test_an_issued_invoice_and_its_lines_cannot_change(self):
        invoice = self.issued()
        invoice.net_total = D('1.00')
        with self.assertRaises(ValidationError):
            invoice.save()
        with self.assertRaises(ValidationError):
            Invoice.objects.get(pk=invoice.pk).delete()
        line = invoice.lines.first()
        line.net = D('0.01')
        with self.assertRaises(ValidationError):
            line.save()
        with self.assertRaises(ValidationError):
            line.delete()
        with self.assertRaises(ValidationError):
            InvoiceLine.objects.create(invoice=invoice, order_line=line.order_line, product=line.product,
                                       description='x', unit_price=D('1'), vat_rate=D('0'), net=D('1'), vat=D('0'),
                                       gross=D('1'))
        fresh = Invoice.objects.get(pk=invoice.pk)
        fresh.email_status = 'sent'
        fresh.save(update_fields=['email_status'])  # the email fields may still change

    def test_snapshots_survive_edits(self):
        invoice = self.issued()
        self.assertEqual((invoice.seller_name, invoice.seller_vat_number, invoice.customer_vat_number,
                          invoice.customer_name, invoice.invoice_date, invoice.due_date),
                         ('Fake Foods Ltd', 'GB123456789', 'GB987654321', 'E2E Fake Bistro Ltd', ISSUED_ON,
                          date(2026, 11, 23)))
        Company.objects.filter(pk=self.company.pk).update(name='Renamed Ltd', vat_number='GB222222222')
        TradeCustomer.objects.filter(pk=self.customer.pk).update(legal_name='Renamed Bistro', vat_number='')
        invoice.refresh_from_db()
        self.assertEqual((invoice.seller_name, invoice.seller_vat_number, invoice.customer_name,
                          invoice.customer_vat_number), ('Fake Foods Ltd', 'GB123456789', 'E2E Fake Bistro Ltd',
                                                         'GB987654321'))
        self.assertEqual(invoice.lines.count(), 4)


class EmailTests(Base):
    def send(self, invoice):
        invoices.send_invoice_email(invoice.pk)
        invoice.refresh_from_db()
        return invoice

    def test_issue_queues_the_email_after_commit(self):
        invoice = create_invoice(self.mixed_order(), self.staff)
        with mock.patch.object(invoices.send_invoice_email, 'apply_async') as queued, \
                self.captureOnCommitCallbacks(execute=True):
            issue(invoice, self.staff)
        queued.assert_called_once_with((invoice.pk,), retry=False)
        invoice.refresh_from_db()
        self.assertEqual(invoice.email_status, 'queued')

    def test_sent_to_the_invoice_email_with_the_pdf(self):
        invoice = self.send(self.issued())
        self.assertEqual((invoice.email_status, invoice.emailed_to), ('sent', 'accounts@example.test'))
        [message] = mail.outbox
        self.assertEqual(message.to, ['accounts@example.test'])
        self.assertIn('INV-000001', message.subject)
        [(name, content, kind)] = message.attachments
        self.assertEqual((name, kind), ('INV-000001.pdf', 'application/pdf'))
        self.assertTrue(content.startswith(b'%PDF'))

    def test_falls_back_to_the_primary_contact_then_none(self):
        TradeCustomer.objects.filter(pk=self.customer.pk).update(invoice_email='')
        TradeCustomerContact.objects.create(customer=self.customer, name='Fake Chef', email='chef@example.test',
                                            is_primary=True)
        self.assertEqual(self.send(self.issued()).emailed_to, 'chef@example.test')
        TradeCustomerContact.objects.all().delete()
        invoice = self.send(self.issued())
        self.assertEqual((invoice.email_status, invoice.emailed_to), ('no_address', ''))

    def test_a_failure_is_recorded_and_never_raises(self):
        invoice = self.issued()
        with mock.patch('wholesale.invoices.EmailMessage.send', side_effect=OSError('smtp down')):
            invoice = self.send(invoice)
        self.assertEqual((invoice.email_status, invoice.status), ('failed', 'issued'))

    def test_a_draft_is_never_emailed(self):
        invoice = create_invoice(self.mixed_order(), self.staff)
        self.assertEqual(self.send(invoice).email_status, 'not_sent')
        self.assertEqual(mail.outbox, [])


class ReviewFixTests(Base):
    def test_the_email_task_is_registered_through_wholesale_tasks(self):
        from config.celery import app

        import wholesale.tasks  # noqa: F401 - what the worker's autodiscovery imports

        self.assertIn('wholesale.invoices.send_invoice_email', app.tasks)

    def test_a_pdf_failure_is_recorded_not_raised(self):
        invoice = self.issued()
        with mock.patch('wholesale.invoice_pdf.build_invoice_pdf', side_effect=ValueError('bad text')):
            invoices.send_invoice_email(invoice.pk)
        invoice.refresh_from_db()
        self.assertEqual(invoice.email_status, 'failed')

    def test_discarding_takes_drafts_only_and_rechecks_under_the_lock(self):
        invoice = create_invoice(self.mixed_order(), self.staff)
        stale = Invoice.objects.get(pk=invoice.pk)  # read as a draft...
        with mock.patch.object(invoices.send_invoice_email, 'apply_async'):
            issue(invoice, self.staff)  # ...then issued meanwhile
        with self.assertRaises(InvoiceError):
            invoices.discard_draft(stale)
        self.assertTrue(Invoice.objects.filter(pk=invoice.pk, status='issued').exists())
        draft = create_invoice(self.mixed_order(), self.staff)
        invoices.discard_draft(draft)
        self.assertFalse(Invoice.objects.filter(pk=draft.pk).exists())
