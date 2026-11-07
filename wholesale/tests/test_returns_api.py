"""G45: returns and credit notes over the API. All names are fake."""

import json
import shutil
import tempfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK
from wholesale import credit_notes, invoices
from wholesale.invoices import create_invoice, issue
from wholesale.models import CreditNote, CustomerReturn, SalesOrder, SalesOrderLine, TradeCustomer
from wholesale.runs import complete_run, start_run
from wholesale.tests.test_runs import Base
from wholesale.tests.test_runs_api import PNG, operator_client

D = Decimal


class ReturnsApiTests(Base):
    def setUp(self):
        super().setUp()
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        settings = override_settings(MEDIA_ROOT=media)
        settings.enable()
        self.addCleanup(settings.disable)
        for target in (invoices.send_invoice_email, credit_notes.send_credit_note_email):
            patcher = mock.patch.object(target, 'apply_async')
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.driver_api = operator_client(self.driver)

    def line(self, order, product):
        return SalesOrderLine.objects.get(order=order, product=product, substitute_for__isnull=True).pk

    def out(self, order):
        run = self.plan(order)
        start_run(run, self.staff)
        return run

    def deliver_url(self, run):
        return reverse('deliveryrun-deliver', args=[run.pk, run.stops.get().pk])

    def test_refusal_at_the_door_json_and_multipart(self):
        first, second = self.picked_order(rice=3), self.picked_order(rice=2)
        run = self.plan(first, second)
        start_run(run, self.staff)
        stops = {stop.order_id: stop.pk for stop in run.stops.all()}
        response = self.driver_api.post(
            reverse('deliveryrun-deliver', args=[run.pk, stops[first.pk]]),
            {'received_by': 'Fake Chef', 'refused': [{'line': self.line(first, self.rice), 'quantity': 1,
                                                       'reason': 'damaged'}]}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        response = self.driver_api.post(
            reverse('deliveryrun-deliver', args=[run.pk, stops[second.pk]]),
            {'received_by': 'Fake Chef', 'photo': SimpleUploadedFile('door.png', PNG),
             'refused': json.dumps([{'line': self.line(second, self.rice), 'quantity': 2, 'reason': 'not_wanted'}])},
            format='multipart')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(CustomerReturn.objects.filter(kind='door').count(), 2)
        bad = self.driver_api.post(self.deliver_url(self.out(self.picked_order())), {
            'received_by': 'Fake Chef', 'refused': [{'line': 1, 'quantity': 1, 'reason': 'lost'}]}, format='json')
        self.assertEqual(bad.status_code, 400)

    def test_later_return_and_credit_note(self):
        order = self.picked_order(rice=3)
        invoice = issue(create_invoice(order, self.staff), self.staff)
        run = self.out(order)
        self.api.post(self.deliver_url(run), {'received_by': 'Fake Chef'}, format='json')
        complete_run(run, self.staff)
        url = reverse('salesorder-returns', args=[order.pk])
        self.assertEqual(self.member.post(url, {}, format='json').status_code, 403)
        response = self.api.post(url, {'note': 'Fake note', 'lines': [
            {'line': self.line(order, self.rice), 'quantity': 2, 'reason': 'not_wanted'}]}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        returned = response.json()
        self.assertEqual((returned['number_display'], returned['kind'], returned['lines'][0]['batch_number']),
                         ('RET-000001', 'later', 'RICE-1'))
        too_many = self.api.post(url, {'lines': [{'line': self.line(order, self.rice), 'quantity': 2,
                                                  'reason': 'damaged'}]}, format='json')
        self.assertEqual(too_many.status_code, 400)

        draft = self.api.post(reverse('creditnote-list'), {'customer_return': returned['id']}, format='json')
        self.assertEqual(draft.status_code, 201, draft.content)
        self.assertEqual((draft.json()['invoice'], draft.json()['gross_total']), (invoice.pk, '4.00'))
        issued = self.api.post(reverse('creditnote-issue', args=[draft.json()['id']]))
        self.assertEqual((issued.status_code, issued.json()['number_display']), (200, 'CN-000001'))
        pdf = self.api.get(reverse('creditnote-pdf', args=[draft.json()['id']]))
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.assertEqual(self.api.delete(reverse('creditnote-detail', args=[draft.json()['id']])).status_code, 400)
        self.assertEqual(self.api.post(reverse('creditnote-email', args=[draft.json()['id']])).status_code, 200)
        listed = self.member.get(reverse('customerreturn-list'), {'order': order.pk}).json()
        self.assertEqual(listed['count'], 1)

    def test_roles_and_tenancy(self):
        stock, _ = get_user_model().objects.create_operator(self.company, 'Fake Stock', STOCK)
        stock_api = operator_client(stock)
        for url in (reverse('customerreturn-list'), reverse('creditnote-list')):
            self.assertEqual(stock_api.get(url).status_code, 403)
            self.assertEqual(self.driver_api.get(url).status_code, 403)
        other = CompanyFactory()
        theirs = SalesOrder.objects.create(company=other, number=1, customer=TradeCustomer.objects.create(
            company=other, account_code='T-1', legal_name='E2E Fake Theirs'), delivery_date=self.today, line1='x',
            town='y', postcode='TT1 1TT', status='delivered')
        self.assertEqual(self.api.post(reverse('salesorder-returns', args=[theirs.pk]), {'lines': []},
                                       format='json').status_code, 404)
        their_return = CustomerReturn.objects.create(company=other, number=1, order=theirs, kind='later',
                                                     status='received')
        self.assertEqual(self.api.post(reverse('creditnote-list'), {'customer_return': their_return.pk},
                                       format='json').status_code, 400)
        self.assertEqual(self.api.get(reverse('customerreturn-detail', args=[their_return.pk])).status_code, 404)
        self.assertFalse(CreditNote.objects.exists())
