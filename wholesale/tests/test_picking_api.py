"""G42: picking through the API, the pick-list PDF, and who may pick or approve. All names are fake."""

import base64
import re
import zlib
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.ledger import move, virtual_location
from inventory.models import Location, Product
from tenants.roles import STOCK, TILL
from wholesale.models import SalesOrder, SalesOrderPick, TradeCustomer, TradeCustomerAddress

D = Decimal


def pdf_text(content):
    """The text drawn on the pages of a (possibly compressed) PDF, in order."""
    chunks = []
    for stream in re.findall(rb'stream\r?\n(.*?)\s*endstream', content, re.S):
        if stream.rstrip().endswith(b'~>'):  # reportlab: ASCII85, then Flate
            stream = base64.a85decode(stream.strip(), adobe=True)
        try:
            stream = zlib.decompress(stream)
        except zlib.error:
            pass
        chunks += re.findall(rb'\((.*?)\)\s*Tj', stream)
    return ' '.join(chunk.decode('latin-1') for chunk in chunks)


class PickingApiTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.company = CompanyFactory()
        self.staff_user = UserFactory(company=self.company, is_staff=True)
        self.staff = APIClient()
        self.staff.force_authenticate(self.staff_user)
        self.approver = APIClient()
        self.approver.force_authenticate(UserFactory(company=self.company, is_staff=True))
        self.warehouse = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse',
                                                 type=Location.WAREHOUSE)
        self.customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                     legal_name='E2E Fake Bistro Ltd')
        TradeCustomerAddress.objects.create(customer=self.customer, label='Kitchen', line1='1 Fake Street',
                                            town='Testtown', postcode='TT1 1TT', instructions='Ring the bell',
                                            is_default=True)
        self.rice = ProductFactory(company=self.company, name='Fake Rice', stock_quantity=0,
                                   wholesale_price=D('2.0000'))
        self.peas = ProductFactory(company=self.company, name='Fake Peas', stock_quantity=0,
                                   wholesale_price=D('1.5000'), storage_zone='frozen')
        self.milk = ProductFactory(company=self.company, name='Fake Milk', stock_quantity=0,
                                   wholesale_price=D('0.9000'), storage_zone='chilled')
        self.lamb = ProductFactory(company=self.company, name='Fake Lamb', stock_quantity=0, sold_by=Product.WEIGHT,
                                   price_per_kg=D('12.99'), wholesale_price=D('9.0000'))
        self.beans = ProductFactory(company=self.company, name='Fake Beans', stock_quantity=0,
                                    wholesale_price=D('1.0000'))
        for product, quantity, kg, number in ((self.rice, 20, None, 'RICE-1'), (self.peas, 20, None, 'PEAS-1'),
                                              (self.milk, 20, None, 'MILK-1'), (self.lamb, 0, D('30.000'), 'LAMB-1'),
                                              (self.beans, 20, None, 'BEANS-1')):
            self.stock(product, quantity, kg, number)
        body = {'customer': self.customer.pk, 'delivery_date': str(self.today + timedelta(days=1)), 'lines': [
            {'product': self.lamb.pk, 'weight_kg': '10.000'}, {'product': self.peas.pk, 'quantity': 4},
            {'product': self.rice.pk, 'quantity': 5}, {'product': self.milk.pk, 'quantity': 6}]}
        self.order_id = self.staff.post(reverse('salesorder-list'), body, format='json').json()['id']
        self.assertEqual(self.post('confirm').status_code, 200)

    def stock(self, product, quantity, kg, number):
        batch = StockBatchFactory(product=product, quantity=0, weight_kg=D('0'), location=self.warehouse,
                                  batch_number=number, expiration_date=self.today + timedelta(days=20))
        with transaction.atomic():
            move(batch, quantity, 'inbound', counterpart=virtual_location(self.company, 'supplier'),
                 weight_kg=kg if product.is_weighed else None)

    def url(self, action, *args):
        return reverse(f'salesorder-{action}', args=[self.order_id, *args])

    def post(self, action, *args, client=None, **body):
        return (client or self.staff).post(self.url(action, *args), body, format='json')

    def line_id(self, product):
        lines = self.staff.get(reverse('salesorder-detail', args=[self.order_id])).json()['lines']
        return next(line['id'] for line in lines if line['product'] == product.pk and not line['substitute_for'])

    def operator(self, role):
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', role)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        return api

    def test_pick_list_pdf(self):
        response = self.staff.get(self.url('pick-list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))
        text = pdf_text(response.content)
        self.assertIn('SO-000001', text)
        self.assertIn('RICE-1', text)  # the reserved lot
        zones = [text.index(zone) for zone in ('Ambient', 'Chilled', 'Frozen', 'Butchery')]
        self.assertEqual(zones, sorted(zones))
        self.assertLess(text.index('Fake Rice'), text.index('Fake Milk'))
        self.assertLess(text.index('Fake Peas'), text.index('Fake Lamb'))

    def test_pick_through_the_api(self):
        self.assertEqual(self.post('start-pick').status_code, 200)
        created = self.post('picks', line=self.line_id(self.lamb), batch_number='LAMB-1', weight_kg='10.400')
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual((created.json()['batch_number'], created.json()['weight_kg']), ('LAMB-1', '10.400'))
        mistake = self.post('picks', line=self.line_id(self.rice), batch_number='RICE-1', quantity=2).json()
        self.assertEqual(self.staff.delete(self.url('pick', mistake['id'])).status_code, 204)
        self.post('picks', line=self.line_id(self.rice), batch_number='RICE-1', quantity=5)
        bad = self.post('picks', line=self.line_id(self.rice), batch_number='RICE-1', quantity=1)
        self.assertEqual(bad.status_code, 400)
        self.assertIn('ordered', bad.json()['detail'])
        self.assertEqual(len(self.staff.get(self.url('picks')).json()), 2)
        finished = self.post('finish-pick')
        self.assertEqual(finished.status_code, 200, finished.content)
        lines = {line['product']: line for line in finished.json()['lines']}
        self.assertEqual(finished.json()['status'], 'picked')
        self.assertEqual(lines[self.lamb.pk]['picked_weight_kg'], '10.400')
        self.assertEqual((lines[self.rice.pk]['picked_quantity'], lines[self.milk.pk]['picked_quantity']), (5, 0))
        self.assertEqual(lines[self.lamb.pk]['zone'], 'butchery')
        self.assertEqual(lines[self.lamb.pk]['picks'][0]['batch_number'], 'LAMB-1')

    def test_substitution_through_the_api(self):
        self.post('start-pick')
        proposed = self.post('substitutions', line=self.line_id(self.peas), product=self.beans.pk, quantity=4)
        self.assertEqual(proposed.status_code, 201, proposed.content)
        substitute = proposed.json()['id']
        self.assertEqual(proposed.json()['substitution'], 'proposed')
        self.assertEqual(self.post('approve-substitution', substitute).status_code, 400)  # the proposer
        approved = self.post('approve-substitution', substitute, client=self.approver)
        self.assertEqual(approved.status_code, 200, approved.content)
        line = next(line for line in approved.json()['lines'] if line['id'] == substitute)
        self.assertEqual((line['substitution'], line['unit_price']), ('approved', '1.0000'))
        other = self.post('substitutions', line=self.line_id(self.milk), product=self.beans.pk, quantity=1).json()
        self.assertEqual(self.post('reject-substitution', other['id'], client=self.approver).status_code, 200)

    def test_stock_operator_picks_but_cannot_approve_and_till_is_refused(self):
        stock = self.operator(STOCK)
        self.assertEqual(stock.get(reverse('salesorder-detail', args=[self.order_id])).status_code, 200)
        self.assertEqual(stock.get(self.url('pick-list')).status_code, 200)
        self.assertEqual(self.post('start-pick', client=stock).status_code, 200)
        pick = self.post('picks', client=stock, line=self.line_id(self.rice), batch_number='RICE-1', quantity=1)
        self.assertEqual(pick.status_code, 201, pick.content)
        self.assertEqual(stock.delete(self.url('pick', pick.json()['id'])).status_code, 204)
        proposed = self.post('substitutions', client=stock, line=self.line_id(self.peas), product=self.beans.pk,
                             quantity=4)
        self.assertEqual(proposed.status_code, 201)
        self.assertEqual(self.post('approve-substitution', proposed.json()['id'], client=stock).status_code, 403)
        self.assertEqual(self.post('confirm', client=stock).status_code, 403)
        self.assertEqual(stock.post(reverse('salesorder-list'), {}, format='json').status_code, 403)
        till = self.operator(TILL)
        for response in (till.get(self.url('pick-list')), self.post('start-pick', client=till)):
            self.assertEqual(response.status_code, 403)
        member = APIClient()
        member.force_authenticate(UserFactory(company=self.company))
        self.assertEqual(self.post('picks', client=member, line=self.line_id(self.rice), batch_number='RICE-1',
                                   quantity=1).status_code, 201)  # any company member may pick

    def test_tenancy(self):
        theirs = SalesOrder.objects.create(company=CompanyFactory(), number=1, customer=TradeCustomer.objects.create(
            company=CompanyFactory(), account_code='T-1', legal_name='E2E Fake Theirs'), delivery_date=self.today,
            line1='9 Fake Lane', town='Elsewhere', postcode='TT9 9TT', status='picking')
        for response in (self.staff.get(reverse('salesorder-pick-list', args=[theirs.pk])),
                         self.staff.post(reverse('salesorder-finish-pick', args=[theirs.pk]))):
            self.assertEqual(response.status_code, 404)
        self.post('start-pick')
        foreign = ProductFactory(company=CompanyFactory(), wholesale_price=D('1'))
        self.assertEqual(self.post('substitutions', line=self.line_id(self.peas), product=foreign.pk,
                                   quantity=1).status_code, 400)
        StockBatchFactory(product=foreign, batch_number='RICE-1')  # another company's lot with the same number
        self.assertEqual(self.post('picks', line=self.line_id(self.rice), batch_number='RICE-1',
                                   quantity=1).status_code, 201)
        self.assertEqual(SalesOrderPick.objects.get().batch.product, self.rice)  # its own lot, never theirs
