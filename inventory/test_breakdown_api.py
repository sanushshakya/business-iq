# inventory/test_breakdown_api.py

"""G21: the breakdown API — tenancy, roles, retries, and output batches selling FEFO at the till."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from inventory.models import Breakdown, Location, StockBatch, YieldTemplate
from inventory.test_breakdowns import BreakdownTestCase
from sales.models import SaleAllocation, TillSession
from tenants.models import Till
from tenants.roles import STOCK, TILL

D = Decimal


class BreakdownApiTests(BreakdownTestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.list_url = reverse('breakdown-list')

    def body(self, **fields):
        return {'template': self.template.pk, 'location': self.butchery.pk, 'input_batch': self.input.pk,
                'input_kg': '10.000', 'loss_reason': 'Fake: drip loss',
                'lines': [{'output': self.out_steak.pk, 'weight_kg': '3.000'},
                          {'output': self.out_mince.pk, 'weight_kg': '4.500'},
                          {'output': self.out_bone.pk, 'weight_kg': '1.500'},
                          {'output': self.out_fat.pk, 'weight_kg': '0.650'}], **fields}

    def post(self, api=None, **fields):
        return (api or self.api).post(self.list_url, self.body(**fields), format='json')

    def operator(self, role):
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', role)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        return api

    def test_record(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body['reference'], body['status'], body['input_kg'], body['loss_kg'],
                          body['expected_loss_kg']), ('BD-000001', 'completed', '10.000', '0.350', '0.300'))
        self.assertEqual(body['input_batch_number'], 'SIDE-1')
        steak = body['lines'][0]
        self.assertEqual((steak['name'], steak['weight_kg'], steak['expected_kg'], steak['expiration_date']),
                         ('Fake Rump Steak', '3.000', '3.000', str(self.today + timedelta(days=5))))
        self.assertEqual(StockBatch.objects.get(pk=steak['output_batch']).weight_kg, D('3.000'))
        self.assertIsNone(body['lines'][2]['output_batch'])
        self.assertEqual(Breakdown.objects.get().created_by, self.user)

    def test_service_refusal_is_a_400(self):
        response = self.post(loss_reason='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('loss_reason', response.json())

    def test_retry_returns_the_first(self):
        ref = str(uuid.uuid4())
        first = self.post(client_ref=ref)
        again = self.post(client_ref=ref)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(first.json()['id'], again.json()['id'])

    def test_other_companys_breakdown_is_404_and_not_listed(self):
        self.assertEqual(self.post().status_code, 201)
        stranger = APIClient()
        stranger.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True))
        detail = reverse('breakdown-detail', args=[Breakdown.objects.get().pk])
        self.assertEqual(stranger.get(detail).status_code, 404)
        self.assertEqual(stranger.post(f'{detail}void/', {'reason': 'x'}, format='json').status_code, 404)
        self.assertEqual(stranger.get(self.list_url).json()['results'], [])

    def test_sent_company_is_ignored(self):
        response = self.post(company=CompanyFactory().pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Breakdown.objects.get().company, self.company)

    def test_another_companys_rows_refused(self):
        other = CompanyFactory()
        their_location = Location.objects.filter(company=other, type='waste').get()
        their_template = YieldTemplate.objects.create(company=other, name='Theirs', input_product=self.weighed(
            'Their Side'))
        for field, value in (('location', their_location.pk), ('template', their_template.pk)):
            with self.subTest(field=field):
                response = self.post(**{field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
        their_batch = StockBatch.objects.create(
            product=their_template.input_product, batch_number='THEIRS', quantity=0, weight_kg=D('1.000'),
            expiration_date=self.today + timedelta(days=5),
            location=Location.objects.create(company=other, code='X', name='X', type='warehouse'))
        self.assertIn('input_batch', self.post(input_batch=their_batch.pk).json())
        other_output = self.output(product=self.steak, percent='10', template=their_template)
        response = self.post(lines=[{'output': other_output.pk, 'weight_kg': '1.000'}])
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('Fake Rump Steak', str(response.json()))  # nothing about their template leaks

    def test_void(self):
        breakdown_id = self.post().json()['id']
        url = reverse('breakdown-void', args=[breakdown_id])
        self.assertEqual(self.api.post(url, {}, format='json').status_code, 400)
        response = self.api.post(url, {'reason': 'Fake: wrong batch'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()['status'], response.json()['void_reason']), ('voided', 'Fake: wrong batch'))
        self.assertEqual(StockBatch.objects.get(pk=self.input.pk).weight_kg, D('10.000'))
        self.assertEqual(self.api.post(url, {'reason': 'again'}, format='json').status_code, 400)

    def test_only_staff_or_who_recorded_it_can_void(self):
        stock = self.operator(STOCK)
        mine = self.post(api=stock).json()['id']
        member = APIClient()
        member.force_authenticate(UserFactory(company=self.company, is_staff=False))
        url = reverse('breakdown-void', args=[mine])
        self.assertEqual(member.post(url, {'reason': 'Fake'}, format='json').status_code, 403)
        self.assertEqual(self.operator(STOCK).post(url, {'reason': 'Fake'}, format='json').status_code, 403)
        self.assertEqual(self.api.post(url, {'reason': 'Fake'}, format='json').status_code, 200)  # staff

    def test_ordinary_members_cannot_record(self):
        member = APIClient()
        member.force_authenticate(UserFactory(company=self.company, is_staff=False))
        self.assertEqual(self.post(api=member).status_code, 403)
        self.assertEqual(member.get(self.list_url).status_code, 200)

    def test_deadlock_is_a_409(self):
        from unittest import mock
        from django.db import OperationalError
        with mock.patch('inventory.breakdowns.record_breakdown', side_effect=OperationalError('deadlock detected')):
            response = self.post()
        self.assertEqual(response.status_code, 409)
        breakdown_id = self.post().json()['id']
        with mock.patch('inventory.breakdowns.void_breakdown', side_effect=OperationalError('deadlock detected')):
            response = self.api.post(reverse('breakdown-void', args=[breakdown_id]), {'reason': 'x'}, format='json')
        self.assertEqual(response.status_code, 409)

    def test_ledger_refusal_is_a_400(self):
        from unittest import mock
        from inventory.ledger import LedgerError
        with mock.patch('inventory.breakdowns.record_breakdown', side_effect=LedgerError('Fake: would go below 0')):
            response = self.post()
        self.assertEqual((response.status_code, response.json()), (400, {'detail': ['Fake: would go below 0']}))

    def test_list_filters_status(self):
        self.post()
        self.assertEqual(len(self.api.get(self.list_url, {'status': 'voided'}).json()['results']), 0)
        self.assertEqual(len(self.api.get(self.list_url, {'status': 'completed'}).json()['results']), 1)

    def test_no_edit_or_delete(self):
        detail = reverse('breakdown-detail', args=[self.post().json()['id']])
        self.assertEqual(self.api.put(detail, self.body(), format='json').status_code, 405)
        self.assertEqual(self.api.patch(detail, {'loss_reason': 'x'}, format='json').status_code, 405)
        self.assertEqual(self.api.delete(detail).status_code, 405)

    def test_stock_operator_records_and_voids_till_operator_cannot(self):
        stock = self.operator(STOCK)
        response = self.post(api=stock)
        self.assertEqual(response.status_code, 201, response.content)
        void = stock.post(reverse('breakdown-void', args=[response.json()['id']]), {'reason': 'Fake'}, format='json')
        self.assertEqual(void.status_code, 200, void.content)
        till = self.operator(TILL)
        self.assertEqual(till.get(self.list_url).status_code, 403)
        self.assertEqual(self.post(api=till).status_code, 403)

    def test_output_batches_sell_fefo(self):
        older = self.batch(self.mince, kg='5.000', days=4, number='MINCE-OLD')  # expires after the breakdown's mince
        breakdown_id = self.post().json()['id']
        made = StockBatch.objects.get(product=self.mince, batch_number='BD-000001')
        self.assertLess(made.expiration_date, older.expiration_date)
        till = Till.objects.create(branch=self.branch, number=1)
        session = TillSession.objects.create(till=till, opened_by=self.user, opening_float=D('0.00'))
        response = self.api.post(reverse('sale-list'), {'session': session.pk, 'payment_method': 'card', 'card_auth_code': 'AUTH01', 'items': [
            {'product': self.mince.pk, 'weight_kg': '1.000'}]}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        allocation = SaleAllocation.objects.get()
        self.assertEqual((allocation.batch_id, allocation.weight_kg), (made.pk, D('1.000')))
        self.assertEqual(StockBatch.objects.get(pk=older.pk).weight_kg, D('5.000'))
        self.assertTrue(Breakdown.objects.filter(pk=breakdown_id).exists())
