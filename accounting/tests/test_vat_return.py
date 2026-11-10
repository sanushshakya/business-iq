"""G54: the nine-box VAT return from the journal, reconciled to the documents; filing stores it and locks its months."""

from datetime import date, timedelta
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

    def test_a_later_void_reduces_the_period_it_falls_in(self):
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


    def test_a_credit_note_reduces_boxes_1_and_6(self):
        from wholesale.credit_notes import create_credit_note, issue_credit_note
        from wholesale.returns import record_return
        from wholesale.runs import complete_run, deliver, start_run

        order = self.first.order
        run = self.plan(order)
        start_run(run, self.staff)
        deliver(run.stops.get(), self.driver, received_by='Fake Chef')
        complete_run(run, self.staff)
        returned = record_return(order, self.staff, [
            {'line': self.line(order, self.rice).pk, 'reason': 'not_wanted', 'quantity': 1}])
        note = issue_credit_note(create_credit_note(returned, self.staff), self.staff)
        self.post_range()
        boxes = vat_boxes(self.company, *self.month())
        invoices = [self.first, self.second]
        self.assertEqual(boxes['box1'], sum(i.vat_total for i in invoices) - note.vat_total)
        self.assertEqual(boxes['box6'], D(int(sum(i.net_total for i in invoices) - note.net_total)))

    def test_suspense_in_the_period_is_flagged(self):
        self.assertEqual(vat_boxes(self.company, *self.month())['suspense'], D('0.00'))
        self.sale('cash', [(None, D('3.00'), D('0'))])  # before VAT was recorded: to Suspense
        self.post_range()
        self.assertEqual(vat_boxes(self.company, *self.month())['suspense'], D('-3.00'))


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

    def test_filing_posts_what_is_not_posted_yet_first(self):
        last_day = self.end
        self.sale('cash', [('standard', D('10.00'), D('2.00'))], day=last_day)  # the nightly run has not run
        filed = file_return(self.company, self.start, self.end, self.staff)
        self.assertEqual((filed.box1, filed.box6), (D('2.00'), D('10')))

    def test_a_posting_problem_stops_filing(self):
        Account.objects.filter(company=self.company, system_key='wastage').update(system_key=None)
        with self.assertRaises(VatReturnError):
            file_return(self.company, self.start, self.end, self.staff)
        self.assertFalse(VatReturn.objects.exists())

    def test_a_filed_month_is_not_reopened(self):
        from accounting.journal import reopen_period

        filed = file_return(self.company, self.start, self.end, self.staff)
        with self.assertRaises(JournalError):
            reopen_period(self.company, self.start, self.staff, 'Fake late bill')
        filed.refresh_from_db()
        self.assertEqual((filed.box4, filed.box7), (D('2.00'), D('10')))

    def test_filing_settles_into_vat_due_and_the_next_period_is_clean(self):
        file_return(self.company, self.start, self.end, self.staff)
        [settlement] = Journal.objects.filter(company=self.company, source='vat_return')
        self.assertEqual(settlement.date, self.end)
        self.assertEqual(sorted((line.account.system_key, line.debit, line.credit) for line in settlement.lines.all()),
                         [('vat_hmrc', D('2.00'), D('0')), ('vat_input', D('0'), D('2.00'))])  # 2.00 to reclaim
        # HMRC repays it this month; a new bill this month: only the new bill is in this month's boxes.
        post_journal(self.company, date=self.today, narrative='Fake HMRC repayment', user=self.staff, lines=[
            {'account': self.key('bank'), 'debit': D('2.00')}, {'account': self.key('vat_hmrc'), 'credit': D('2.00')}])
        self.bill(D('30.00'), D('6.00'))
        boxes = vat_boxes(self.company, *self.month())
        self.assertEqual((boxes['box4'], boxes['box7']), (D('6.00'), D('30')))

    def test_reversing_a_waste_reclassification_stays_out_of_box_7(self):
        from accounting.journal import reverse_journal

        self.write_off(D('3.10'))
        self.post_range()
        [waste] = Journal.objects.filter(company=self.company, source='write_off')
        reverse_journal(waste, self.staff, 'Fake: found it', date=self.today)
        self.assertEqual(vat_boxes(self.company, *self.month())['box7'], D('0'))

    def test_only_whole_ended_months_that_do_not_overlap(self):
        for start, end in ((self.start + timedelta(days=1), self.end),  # not from a 1st
                           (self.start, self.end - timedelta(days=1)),  # not to a month end
                           (self.today.replace(day=1), self.today),  # not over yet
                           (self.end, self.start),
                           (date(1999, 1, 1), date(1999, 3, 31))):  # before the books can go
            with self.subTest(start=start, end=end), self.assertRaises(VatReturnError):
                file_return(self.company, start, end, self.staff)
        file_return(self.company, self.start, self.end, self.staff)
        with self.assertRaises(VatReturnError):  # overlaps the filed one
            file_return(self.company, (self.start - timedelta(days=1)).replace(day=1), self.end, self.staff)
        self.assertEqual(VatReturn.objects.count(), 1)
        self.assertFalse(Journal.objects.filter(date__gt=self.end, source='manual').exists())
