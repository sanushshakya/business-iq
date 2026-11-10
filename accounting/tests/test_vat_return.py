"""G54: the nine-box VAT return from the journal, reconciled to the documents; filing stores it and locks its months."""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError

from accounting.journal import JournalError, post_journal
from accounting.models import Account, Journal, VatReturn
from accounting.tests.test_postings import PostingCase, at
from accounting.vat_return import VatReturnError, file_return, vat_boxes
from sales.models import Sale, SaleItem
from wholesale.models import InvoiceLine

D = Decimal


class VatReturnCase(PostingCase):
    def month(self):
        return self.today.replace(day=1), self.today

    def account(self, code):
        return Account.objects.get(company=self.company, code=code)

    def bill(self, net, vat, code='5000', day=None):
        """A supplier bill keyed as a manual journal (until bills exist)."""
        lines = [{'account': self.account(code), 'debit': net},
                 {'account': self.key('creditors'), 'credit': net + vat}]
        if vat:
            lines.append({'account': self.key('vat_input'), 'debit': vat})
        return post_journal(self.company, date=day or self.today, narrative='Fake supplier bill', lines=lines,
                            user=self.staff)


class BoxTests(VatReturnCase):
    def test_boxes_reconcile_to_the_source_documents(self):
        self.sale('cash', [('zero', D('2.00'), D('0.00')), ('standard', D('10.00'), D('2.00'))])
        self.sale('card', [('reduced', D('20.00'), D('1.00'))])
        self.sale('cash', [('standard', D('5.00'), D('1.00'))], voided_on=self.today)  # voided: nets to nothing
        self.write_off(D('3.10'))  # a reclassification, not a purchase
        self.bill(D('100.00'), D('20.00'))
        self.bill(D('50.55'), D('0'), code='7200')
        self.bill(D('999.00'), D('0'), code='7000')  # wages: outside box 7
        self.post_range()
        boxes = vat_boxes(self.company, *self.month())
        live = SaleItem.objects.exclude(sale__status='voided')
        invoice_lines = InvoiceLine.objects.filter(invoice__in=[self.first, self.second])
        sales_vat = sum(live.values_list('vat_amount', flat=True)) + sum(invoice_lines.values_list('vat', flat=True))
        sales_net = sum(live.values_list('net_amount', flat=True)) + sum(invoice_lines.values_list('net', flat=True))
        self.assertEqual(boxes['box1'], sales_vat)
        self.assertEqual((boxes['box2'], boxes['box3']), (D('0.00'), sales_vat))
        self.assertEqual(boxes['box4'], D('20.00'))
        self.assertEqual(boxes['box5'], abs(sales_vat - D('20.00')))
        self.assertEqual(boxes['net_direction'], 'pay' if sales_vat >= D('20.00') else 'reclaim')
        self.assertEqual(boxes['box6'], D(int(sales_net)))
        self.assertEqual(boxes['box7'], D('150'))  # 150.55, pence dropped; waste and wages left out
        self.assertEqual((boxes['box8'], boxes['box9']), (D('0'), D('0')))

    def test_a_credit_note_and_a_later_void_reduce_the_period_they_fall_in(self):
        sale = self.sale('card', [('standard', D('10.00'), D('2.00'))], day=self.today - timedelta(days=40))
        self.post_range(self.today - timedelta(days=40))
        Sale.objects.filter(pk=sale.pk).update(status='voided', voided_at=at(self.today))
        self.post_range()
        earlier = vat_boxes(self.company, self.today - timedelta(days=40), self.today - timedelta(days=40))
        self.assertEqual((earlier['box1'], earlier['box6']), (D('2.00'), D('10')))
        now = vat_boxes(self.company, self.today, self.today)
        invoiced_vat = self.first.vat_total + self.second.vat_total
        self.assertEqual(now['box1'], invoiced_vat - D('2.00'))

    def test_more_reclaimed_than_due_and_pence_dropped(self):
        quiet_day = self.today - timedelta(days=70)  # a month with no invoices
        self.bill(D('1234.99'), D('246.99'), day=quiet_day)
        boxes = vat_boxes(self.company, quiet_day.replace(day=1), quiet_day)
        self.assertEqual((boxes['box1'], boxes['box4'], boxes['box5'], boxes['net_direction']),
                         (D('0.00'), D('246.99'), D('246.99'), 'reclaim'))
        self.assertEqual(boxes['box7'], D('1234'))

    def test_box7_follows_the_account_flag(self):
        self.bill(D('40.00'), D('8.00'), code='7500')  # bank charges: not flagged by default
        self.assertEqual(vat_boxes(self.company, *self.month())['box7'], D('0'))
        Account.objects.filter(company=self.company, code='7500').update(vat_purchases=True)
        self.assertEqual(vat_boxes(self.company, *self.month())['box7'], D('40'))


class FilingTests(VatReturnCase):
    def setUp(self):
        super().setUp()
        self.start = (self.today.replace(day=1) - timedelta(days=1)).replace(day=1)  # last month
        self.end = self.today.replace(day=1) - timedelta(days=1)
        self.bill(D('10.00'), D('2.00'), day=self.start + timedelta(days=3))

    def test_filing_stores_the_boxes_and_locks_the_months(self):
        filed = file_return(self.company, self.start, self.end, self.staff)
        self.assertEqual((filed.box4, filed.box7, filed.net_direction, filed.filed_by), (D('2.00'), D('10'),
                                                                                         'reclaim', self.staff))
        with self.assertRaises(JournalError):
            self.bill(D('1.00'), D('0'), day=self.end)
        for attempt in (lambda: filed.save(), lambda: filed.delete(), lambda: VatReturn.objects.update(box1=1)):
            with self.assertRaises(ValidationError):
                attempt()

    def test_only_whole_ended_months_that_do_not_overlap(self):
        for start, end in ((self.start + timedelta(days=1), self.end),  # not from a 1st
                           (self.start, self.end - timedelta(days=1)),  # not to a month end
                           (self.today.replace(day=1), self.today),  # not over yet
                           (self.end, self.start)):
            with self.subTest(start=start, end=end), self.assertRaises(VatReturnError):
                file_return(self.company, start, end, self.staff)
        file_return(self.company, self.start, self.end, self.staff)
        with self.assertRaises(VatReturnError):  # overlaps the filed one
            file_return(self.company, (self.start - timedelta(days=1)).replace(day=1), self.end, self.staff)
        self.assertEqual(VatReturn.objects.count(), 1)
        self.assertFalse(Journal.objects.filter(date__gt=self.end, source='manual').exists())
