# inventory/test_write_offs.py

"""G23: waste write-offs — stock goes to the WASTE location; above the company's £ threshold (or with no known cost)
a staff member other than the one who raised it must approve first."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.models import Setting
from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.ledger import move, virtual_location
from inventory.models import Location, StockBatch, StockMovement, WriteOff
from inventory.write_offs import WriteOffError, approve, raise_write_off, reject
from tenants.models import Branch
from tenants.roles import STOCK, TILL
from vat.services import uk_date

D = Decimal


class WriteOffTestCase(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.raiser = UserFactory(company=self.company, is_staff=True)
        self.manager = UserFactory(company=self.company, is_staff=True)
        branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=branch, type='shop_floor')
        self.waste = virtual_location(self.company, Location.WASTE)
        self.rice = ProductFactory(company=self.company, name='Fake Rice', price=D('2.00'), stock_quantity=0)
        self.rice_batch = self.batch(self.rice, pieces=10, cost='0.8000')  # 10 bags at £0.80
        self.mince = ProductFactory(company=self.company, name='Fake Mince', price=D('0.00'), stock_quantity=0,
                                    sold_by='weight', price_per_kg=D('8.9900'))
        self.mince_batch = self.batch(self.mince, kg='5.000', cost='4.0000')  # £4/kg

    def batch(self, product, *, pieces=0, kg=None, cost=None, days=5, number=None):
        batch = StockBatchFactory(product=product, quantity=0, weight_kg=D('0'), location=self.shop,
                                  expiration_date=uk_date() + timedelta(days=days), unit_cost=cost and D(cost),
                                  **({'batch_number': number} if number else {}))
        with transaction.atomic():
            move(batch, pieces, 'inbound', counterpart=virtual_location(self.company, Location.SUPPLIER),
                 weight_kg=None if kg is None else D(kg))
        return StockBatch.objects.get(pk=batch.pk)

    def held(self, batch):
        batch = StockBatch.objects.get(pk=batch.pk)
        return batch.quantity, batch.weight_kg

    def write(self, batch=None, **kwargs):
        args = {'company': self.company, 'user': self.raiser, 'batch': batch or self.rice_batch, 'reason': 'damaged',
                'note': 'Fake: dropped', **kwargs}
        return raise_write_off(**args)


class RaiseTests(WriteOffTestCase):
    def test_under_the_threshold_goes_straight_to_waste(self):
        write_off = self.write(quantity=3)
        self.assertEqual((write_off.reference, write_off.status, write_off.cost), ('W-000001', 'written_off',
                                                                                    D('2.40')))
        self.assertEqual(self.held(self.rice_batch)[0], 7)
        movement = StockMovement.objects.get(write_off=write_off)
        self.assertEqual((movement.movement_type, movement.quantity, movement.source_location_id,
                          movement.destination_location_id), ('outbound', 3, self.shop.pk, self.waste.pk))
        self.assertIsNotNone(write_off.written_off_at)

    def test_weighed_product_by_kg(self):
        write_off = self.write(batch=self.mince_batch, weight_kg=D('1.250'), reason='trim')
        self.assertEqual((write_off.status, write_off.cost, write_off.weight_kg), ('written_off', D('5.00'),
                                                                                   D('1.250')))
        self.assertEqual(self.held(self.mince_batch), (0, D('3.750')))

    def test_over_the_threshold_waits_and_moves_nothing(self):
        Setting.objects.create(company=self.company, key='write_off_approval_threshold', value='2.00')
        write_off = self.write(quantity=3)  # £2.40 > £2.00
        self.assertEqual(write_off.status, 'pending')
        self.assertFalse(StockMovement.objects.filter(write_off=write_off).exists())
        self.assertEqual(self.held(self.rice_batch)[0], 10)

    def test_exactly_the_threshold_goes_through(self):
        Setting.objects.create(company=self.company, key='write_off_approval_threshold', value='2.40')
        self.assertEqual(self.write(quantity=3).status, 'written_off')

    def test_default_threshold_is_50_and_a_bad_setting_falls_back(self):
        big = self.batch(self.rice, pieces=1000, cost='1.0000', number='BIG')
        self.assertEqual(self.write(batch=big, quantity=50).status, 'written_off')  # £50.00
        self.assertEqual(self.write(batch=big, quantity=51).status, 'pending')
        for value in ('NaN', '-5', 'lots'):
            with self.subTest(value=value):
                Setting.objects.update_or_create(company=self.company, key='write_off_approval_threshold',
                                                 defaults={'value': value})
                self.assertEqual(self.write(batch=big, quantity=1).status, 'written_off')
                self.assertEqual(self.write(batch=big, quantity=51).status, 'pending')

    def test_no_cost_on_the_batch_waits_for_approval(self):
        StockBatch.objects.filter(pk=self.rice_batch.pk).update(unit_cost=None)
        write_off = self.write(quantity=1)
        self.assertEqual((write_off.status, write_off.cost), ('pending', None))
        self.assertEqual(self.held(self.rice_batch)[0], 10)

    def test_catch_weight_pieces_take_their_kg(self):
        legs = ProductFactory(company=self.company, name='Fake Lamb Legs', price=D('0.00'), stock_quantity=0,
                              sold_by='weight', is_catch_weight=True, price_per_kg=D('9.0000'))
        batch = self.batch(legs, pieces=3, kg='10.000', cost='6.0000')
        write_off = self.write(batch=batch, quantity=1, reason='quality')
        self.assertEqual((write_off.weight_kg, write_off.cost), (D('3.333'), D('20.00')))
        self.assertEqual(self.held(batch), (2, D('6.667')))
        last = self.write(batch=batch, quantity=2, reason='quality')
        self.assertEqual(last.weight_kg, D('6.667'))
        self.assertEqual(self.held(batch), (0, D('0.000')))

    def test_expired_stock_can_be_written_off(self):
        old = self.batch(self.rice, pieces=4, cost='0.5000', days=-2, number='OLD')
        self.assertEqual(self.write(batch=old, quantity=4, reason='expired').status, 'written_off')
        self.assertEqual(self.held(old)[0], 0)

    def test_amount_rules(self):
        cases = [
            ({'quantity': 0}, 'quantity', 'Say how many to write off.'),
            ({'quantity': 11}, 'quantity', 'Batch {} holds 10.'),
            ({'quantity': 1, 'weight_kg': D('1.000')}, 'weight_kg', 'Fake Rice is sold by each; leave the kg out.'),
        ]
        for kwargs, field, message in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(WriteOffError) as caught:
                    self.write(**kwargs)
                self.assertEqual(caught.exception.detail[field], [message.format(self.rice_batch.batch_number)])
        for kwargs, field in (({'weight_kg': D('0.000')}, 'weight_kg'), ({'weight_kg': D('5.001')}, 'weight_kg'),
                              ({'weight_kg': D('1.0005')}, 'weight_kg'), ({'quantity': 1}, 'quantity')):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(WriteOffError) as caught:
                    self.write(batch=self.mince_batch, **kwargs)
                self.assertIn(field, caught.exception.detail)
        self.assertFalse(WriteOff.objects.exists())

    def test_reason_must_be_known(self):
        with self.assertRaises(WriteOffError) as caught:
            self.write(quantity=1, reason='lost')
        self.assertIn('reason', caught.exception.detail)

    def test_another_companys_batch_refused(self):
        theirs = StockBatchFactory(product=ProductFactory(company=CompanyFactory()), quantity=5)
        with self.assertRaises(WriteOffError) as caught:
            self.write(batch=theirs, quantity=1)
        self.assertEqual(caught.exception.detail['batch'], ['Invalid pk - object does not exist.'])

    def test_write_offs_are_not_demand(self):
        from inventory.projections import average_daily_demand
        self.write(quantity=3)
        self.assertEqual(average_daily_demand(self.rice, days=7), 0)


class DecisionTests(WriteOffTestCase):
    def setUp(self):
        super().setUp()
        Setting.objects.create(company=self.company, key='write_off_approval_threshold', value='1.00')
        self.pending = self.write(quantity=3)  # £2.40

    def test_another_staff_member_approves_and_stock_moves(self):
        done = approve(self.pending, user=self.manager, note='Fake: seen it')
        self.assertEqual((done.status, done.decided_by, done.decision_note), ('written_off', self.manager,
                                                                              'Fake: seen it'))
        self.assertEqual(self.held(self.rice_batch)[0], 7)
        self.assertEqual(StockMovement.objects.get(write_off=done).destination_location_id, self.waste.pk)

    def test_the_raiser_cannot_approve_their_own(self):
        with self.assertRaises(WriteOffError) as caught:
            approve(self.pending, user=self.raiser)
        self.assertEqual(caught.exception.detail['detail'], ['Someone other than who raised it must approve it.'])
        self.assertEqual(self.held(self.rice_batch)[0], 10)

    def test_non_staff_cannot_decide(self):
        member = UserFactory(company=self.company, is_staff=False)
        for step in (approve, reject):
            with self.subTest(step=step.__name__):
                with self.assertRaises(WriteOffError) as caught:
                    step(self.pending, user=member)
                self.assertEqual(caught.exception.detail['detail'], ['Only staff can approve or reject write-offs.'])

    def test_reject_moves_nothing(self):
        done = reject(self.pending, user=self.manager, note='Fake: sell it as reduced')
        self.assertEqual(done.status, 'rejected')
        self.assertFalse(StockMovement.objects.filter(write_off=done).exists())
        self.assertEqual(self.held(self.rice_batch)[0], 10)
        with self.assertRaises(WriteOffError):
            approve(done, user=self.manager)

    def test_approval_rechecks_the_stock(self):
        with transaction.atomic():
            move(self.rice_batch, 9, 'outbound', counterpart=virtual_location(self.company, Location.CUSTOMER))
        with self.assertRaises(WriteOffError) as caught:
            approve(self.pending, user=self.manager)
        self.assertEqual(caught.exception.detail['detail'], [f'Batch {self.rice_batch.batch_number} now holds 1; '
                                                             'raise a new write-off.'])
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'pending')

    def test_cannot_decide_twice(self):
        approve(self.pending, user=self.manager)
        for step in (approve, reject):
            with self.assertRaises(WriteOffError):
                step(self.pending, user=self.manager)
        self.assertEqual(self.held(self.rice_batch)[0], 7)


class WriteOffApiTests(WriteOffTestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.raiser)
        self.list_url = reverse('writeoff-list')

    def operator(self, role):
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', role)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        return api

    def post(self, api=None, **fields):
        body = {'batch': self.rice_batch.pk, 'reason': 'damaged', 'quantity': 2, 'note': 'Fake', **fields}
        return (api or self.api).post(self.list_url, body, format='json')

    def test_raise_and_read(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body['reference'], body['status'], body['cost'], body['product_name'], body['location']),
                         ('W-000001', 'written_off', '1.60', 'Fake Rice', self.shop.pk))
        self.assertEqual(body['threshold'], '50.00')

    def test_stock_operator_raises_staff_approves(self):
        Setting.objects.create(company=self.company, key='write_off_approval_threshold', value='1.00')
        stock = self.operator(STOCK)
        response = self.post(api=stock)
        self.assertEqual((response.status_code, response.json()['status']), (201, 'pending'))
        url = reverse('writeoff-approve', args=[response.json()['id']])
        self.assertEqual(stock.post(url, {}, format='json').status_code, 403)
        manager = APIClient()
        manager.force_authenticate(self.manager)
        approved = manager.post(url, {'note': 'ok'}, format='json')
        self.assertEqual((approved.status_code, approved.json()['status']), (200, 'written_off'))
        self.assertEqual(self.api.post(reverse('writeoff-reject', args=[response.json()['id']]), {},
                                       format='json').status_code, 400)

    def test_list_filters_status(self):
        Setting.objects.create(company=self.company, key='write_off_approval_threshold', value='1.00')
        self.post()
        self.assertEqual(len(self.api.get(self.list_url, {'status': 'pending'}).json()['results']), 1)
        self.assertEqual(len(self.api.get(self.list_url, {'status': 'written_off'}).json()['results']), 0)

    def test_till_operator_and_members_cannot_raise(self):
        self.assertEqual(self.post(api=self.operator(TILL)).status_code, 403)
        member = APIClient()
        member.force_authenticate(UserFactory(company=self.company, is_staff=False))
        self.assertEqual(self.post(api=member).status_code, 403)
        self.assertEqual(member.get(self.list_url).status_code, 200)

    def test_other_company_is_404_and_their_batch_refused(self):
        self.assertEqual(self.post().status_code, 201)
        stranger = APIClient()
        stranger.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True))
        detail = reverse('writeoff-detail', args=[WriteOff.objects.get().pk])
        self.assertEqual(stranger.get(detail).status_code, 404)
        self.assertEqual(stranger.post(f'{detail}approve/', {}, format='json').status_code, 404)
        self.assertEqual(stranger.get(self.list_url).json()['results'], [])
        theirs = StockBatchFactory(product=ProductFactory(company=CompanyFactory()), quantity=5)
        self.assertIn('batch', self.post(batch=theirs.pk).json())

    def test_sent_company_and_status_ignored(self):
        response = self.post(company=CompanyFactory().pk, status='written_off', cost='0.01')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((WriteOff.objects.get().company, WriteOff.objects.get().cost), (self.company, D('1.60')))

    def test_no_edit_or_delete(self):
        detail = reverse('writeoff-detail', args=[self.post().json()['id']])
        self.assertEqual(self.api.patch(detail, {'note': 'x'}, format='json').status_code, 405)
        self.assertEqual(self.api.delete(detail).status_code, 405)
