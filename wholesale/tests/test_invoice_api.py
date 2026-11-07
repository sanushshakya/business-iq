"""G43: invoices over the API, the PDF snapshot, and the company's VAT number. All names are fake."""

import os

from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.models import Company
from tenants.roles import STOCK
from wholesale import invoices
from wholesale.invoice_pdf import build_invoice_pdf
from wholesale.models import Invoice, SalesOrder, TradeCustomer
from wholesale.tests.test_invoices import ISSUED_ON, Base
from wholesale.tests.test_picking_api import pdf_text

D = Decimal
SNAPSHOT = Path(__file__).parent / 'snapshots' / 'invoice.txt'


class InvoiceApiTests(Base):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        queued = mock.patch.object(invoices.send_invoice_email, 'apply_async')
        self.queued = queued.start()
        self.addCleanup(queued.stop)

    def create(self, order=None, client=None):
        return (client or self.api).post(reverse('invoice-list'), {'order': (order or self.mixed_order()).pk},
                                         format='json')

    def action(self, invoice_id, name, client=None):
        return (client or self.api).post(reverse(f'invoice-{name}', args=[invoice_id]))

    def test_draft_issue_and_pdf(self):
        created = self.create()
        self.assertEqual(created.status_code, 201, created.content)
        data = created.json()
        self.assertEqual((data['status'], data['number'], data['number_display'], data['gross_total']),
                         ('draft', None, f'Draft #{data["id"]}', '476.53'))
        self.assertEqual(len(data['lines']), 4)
        draft_pdf = self.api.get(reverse('invoice-pdf', args=[data['id']]))
        self.assertIn('DRAFT', pdf_text(draft_pdf.content))
        with self.captureOnCommitCallbacks(execute=True):
            issued = self.action(data['id'], 'issue')
        self.assertEqual(issued.status_code, 200, issued.content)
        self.assertEqual((issued.json()['number_display'], issued.json()['email_status'],
                          issued.json()['due_date']), ('INV-000001', 'queued', '2026-11-23'))
        self.queued.assert_called_once()
        self.assertEqual(self.action(data['id'], 'issue').status_code, 400)
        pdf = self.api.get(reverse('invoice-pdf', args=[data['id']]))
        self.assertEqual((pdf.status_code, pdf['Content-Type']), (200, 'application/pdf'))
        self.assertTrue(pdf.content.startswith(b'%PDF'))

    def test_no_update_and_delete_drafts_only(self):
        invoice_id = self.create().json()['id']
        url = reverse('invoice-detail', args=[invoice_id])
        self.assertEqual(self.api.patch(url, {'net_total': '1.00'}, format='json').status_code, 405)
        self.action(invoice_id, 'issue')
        self.assertEqual(self.api.delete(url).status_code, 400)
        draft = self.create().json()['id']
        self.assertEqual(self.api.delete(reverse('invoice-detail', args=[draft])).status_code, 204)
        self.assertFalse(Invoice.objects.filter(pk=draft).exists())

    def test_refresh_and_resend(self):
        order = self.mixed_order()
        invoice_id = self.create(order).json()['id']
        order.lines.filter(product=self.cola).update(picked_quantity=8)
        self.assertEqual(self.action(invoice_id, 'refresh').json()['gross_total'], '477.02')  # +3.32 net +0.66 VAT
        self.assertEqual(self.action(invoice_id, 'email').status_code, 400)  # a draft is not emailed
        self.action(invoice_id, 'issue')
        self.assertEqual(self.action(invoice_id, 'refresh').status_code, 400)
        Invoice.objects.filter(pk=invoice_id).update(email_status='failed')
        with self.captureOnCommitCallbacks(execute=True):
            resent = self.action(invoice_id, 'email')
        self.assertEqual((resent.status_code, resent.json()['email_status']), (200, 'queued'))

    def test_refusals(self):
        picking = self.order(status=SalesOrder.PICKING)
        self.assertEqual(self.create(picking).status_code, 400)
        order = self.mixed_order()
        self.create(order)
        self.assertEqual(self.create(order).status_code, 400)

    def test_roles(self):
        invoice_id = self.create().json()['id']
        self.assertEqual(self.member.get(reverse('invoice-detail', args=[invoice_id])).status_code, 200)
        self.assertEqual(self.member.get(reverse('invoice-pdf', args=[invoice_id])).status_code, 200)
        self.assertEqual(self.create(client=self.member).status_code, 403)
        for name in ('issue', 'refresh', 'email'):
            self.assertEqual(self.action(invoice_id, name, self.member).status_code, 403)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', STOCK)
        stock = APIClient()
        stock.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(stock.get(reverse('invoice-list')).status_code, 403)

    def test_tenancy(self):
        other = CompanyFactory(vat_number='GB111111111')
        customer = TradeCustomer.objects.create(company=other, account_code='C-1', legal_name='E2E Fake Other')
        theirs_order = self.order(company=other, customer=customer)
        theirs = Invoice.objects.create(company=other, order=theirs_order)
        self.assertEqual(self.api.get(reverse('invoice-detail', args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.action(theirs.pk, 'issue').status_code, 404)
        self.assertEqual(self.create(self.order(company=other, customer=customer)).status_code, 400)
        admin = APIClient()
        admin.force_authenticate(UserFactory(company=self.company, is_staff=True, is_superuser=True))
        self.assertEqual(self.create(self.order(company=other, customer=customer), client=admin).status_code, 400)
        self.assertEqual(self.api.get(reverse('invoice-list')).json()['count'], 0)

    def test_filters(self):
        first = self.create().json()['id']
        second = self.create().json()['id']
        self.action(second, 'issue')

        def ids(**params):
            response = self.member.get(reverse('invoice-list'), params)
            self.assertEqual(response.status_code, 200, response.content)
            return sorted(row['id'] for row in response.json()['results'])

        self.assertEqual(ids(status='draft'), [first])
        self.assertEqual(ids(status='issued'), [second])
        self.assertEqual(ids(customer=self.customer.pk), [first, second])
        self.assertEqual(self.member.get(reverse('invoice-list'), {'status': 'x'}).status_code, 400)


class InvoicePdfSnapshotTests(Base):
    def test_matches_the_snapshot(self):
        invoice = self.issued()
        text = pdf_text(build_invoice_pdf(invoice)).replace(' ', ' ').strip() + '\n'
        if os.environ.get('UPDATE_SNAPSHOTS') == '1':  # regenerate on purpose only
            SNAPSHOT.parent.mkdir(exist_ok=True)
            SNAPSHOT.write_text(text)
        self.assertEqual(text, SNAPSHOT.read_text())
        for expected in ('GB123456789', 'GB987654321', 'INV-000001', '476.53', 'Due 23/11/2026'):
            self.assertIn(expected, text)

    def test_the_pdf_keeps_the_snapshot_after_edits(self):
        invoice = self.issued()
        before = pdf_text(build_invoice_pdf(invoice))
        Company.objects.filter(pk=self.company.pk).update(name='Renamed Ltd', vat_number='GB222222222')
        TradeCustomer.objects.filter(pk=self.customer.pk).update(legal_name='Renamed Bistro')
        self.assertEqual(pdf_text(build_invoice_pdf(Invoice.objects.get(pk=invoice.pk))), before)
        self.assertEqual(invoice.invoice_date, ISSUED_ON)


class CompanyAndCustomerFieldTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.staff = APIClient()
        self.staff.force_authenticate(UserFactory(company=self.company, is_staff=True))
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))

    def test_company_vat_number(self):
        url = reverse('my-company')
        response = self.staff.patch(url, {'vat_number': 'gb 123 4567 89'}, format='json')
        self.assertEqual((response.status_code, response.json()['vat_number']), (200, 'GB123456789'))
        self.assertEqual(self.staff.patch(url, {'vat_number': 'FR123'}, format='json').status_code, 400)
        self.assertEqual(self.member.patch(url, {'vat_number': ''}, format='json').status_code, 403)
        self.assertEqual(self.member.get(url).json()['vat_number'], 'GB123456789')

    def test_customer_invoice_fields(self):
        body = {'account_code': 'C-9', 'legal_name': 'E2E Fake Nine Ltd', 'invoice_email': 'ap@example.test',
                'payment_terms_days': 45}
        response = self.staff.post(reverse('tradecustomer-list'), body, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()['invoice_email'], response.json()['payment_terms_days']),
                         ('ap@example.test', 45))
        for bad in ({'payment_terms_days': 121}, {'invoice_email': 'not-an-email'}):
            with self.subTest(bad=bad):
                self.assertEqual(self.staff.post(reverse('tradecustomer-list'), {**body, 'account_code': 'C-10',
                                                                                 **bad}, format='json').status_code,
                                 400)
