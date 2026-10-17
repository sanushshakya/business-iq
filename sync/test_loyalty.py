# sync/test_loyalty.py

import json
import uuid
from datetime import timedelta
from unittest import mock

import requests
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from common.tests.factories import CompanyFactory, UserFactory
from sales.tests.test_sales import SalesTestCase
from sync.models import OutboundEvent, StorefrontConnection
from sync.signing import verify
from sync.tasks import deliver_outbound_event, deliver_outbound_events
from tenants.roles import DEMAND, TILL

SECRET = 'test-webhook-secret'
CARD = '2995520383161'
BASE = 'https://m18.example.com/'
CUSTOMER = {'first_name': 'Amira', 'points_balance': 24, 'tier': 'Bronze', 'points_gbp_value': '0.24'}


def reply(status_code, body=None):
    response = mock.Mock(status_code=status_code, text=json.dumps(body or {}))
    response.json.return_value = body or {}
    return response


class LoyaltyTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.connection = StorefrontConnection.objects.create(company=self.company, base_url=BASE, webhook_secret=SECRET)
        self.dispatch = self.enterContext(mock.patch('sync.outbox.deliver_outbound_event.apply_async'))

    def sell_with_card(self, session_id, product, card=CARD, client_ref=None):
        body = {
            'session': session_id, 'payment_method': 'card',
            'items': [{'product': product.pk, 'quantity': 2}],
            'loyalty_barcode': card, 'client_ref': client_ref,
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('sale-list'), body, format='json')
        self.assertIn(response.status_code, (200, 201), response.content)
        return response

    def lookup(self, barcode=CARD, client=None):
        return (client or self.client).post(reverse('loyalty-lookup'), {'barcode': barcode}, format='json')


class AwardOnSaleTests(LoyaltyTestCase):
    def test_sale_with_barcode_emits_award_on_commit(self):
        session, product = self.open(), self.stocked(price='2.50')
        ref = str(uuid.uuid4())
        response = self.sell_with_card(session['id'], product, client_ref=ref)
        self.assertNotIn('loyalty_barcode', response.json())
        event = OutboundEvent.objects.get()
        self.assertEqual((event.connection, event.event_type, event.status), (self.connection, 'loyalty.award', 'pending'))
        self.assertEqual(event.payload['event_id'], str(event.event_id))
        self.assertEqual(event.payload['data'], {'barcode': CARD, 'amount': '5.00', 'order_reference': f'iq-sale-{ref}'})
        self.dispatch.assert_called_once_with((event.pk,), retry=False)

    def test_sale_without_client_ref_uses_sale_pk(self):
        session, product = self.open(), self.stocked()
        sale = self.sell_with_card(session['id'], product).json()
        self.assertEqual(OutboundEvent.objects.get().payload['data']['order_reference'], f"iq-sale-{sale['id']}")

    def test_sale_without_barcode_emits_nothing(self):
        session, product = self.open(), self.stocked()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.sell(session['id'], [(product, 1)], method='card').status_code, 201)
        self.sell_with_card(session['id'], product, card='')
        self.assertFalse(OutboundEvent.objects.exists())
        self.dispatch.assert_not_called()

    def test_sale_without_connection_still_succeeds(self):
        session, product = self.open(), self.stocked(batches=((10, 30),))
        StorefrontConnection.objects.filter(pk=self.connection.pk).update(is_active=False)
        self.assertEqual(self.sell_with_card(session['id'], product).status_code, 201)
        self.connection.delete()
        self.assertEqual(self.sell_with_card(session['id'], product).status_code, 201)
        self.assertFalse(OutboundEvent.objects.exists())

    def test_dispatch_failure_does_not_fail_the_sale(self):
        self.dispatch.side_effect = ConnectionError('broker down')
        session, product = self.open(), self.stocked()
        self.assertEqual(self.sell_with_card(session['id'], product).status_code, 201)
        self.assertEqual(OutboundEvent.objects.get().status, 'pending')  # the sweep sends it later

    def test_client_ref_retry_does_not_emit_twice(self):
        session, product = self.open(), self.stocked()
        ref = str(uuid.uuid4())
        self.assertEqual(self.sell_with_card(session['id'], product, client_ref=ref).status_code, 201)
        self.assertEqual(self.sell_with_card(session['id'], product, client_ref=ref).status_code, 200)
        self.assertEqual(OutboundEvent.objects.count(), 1)
        self.assertEqual(self.dispatch.call_count, 1)

    def test_void_emits_reverse_once(self):
        session, product = self.open(), self.stocked()
        ref = str(uuid.uuid4())
        sale = self.sell_with_card(session['id'], product, client_ref=ref).json()
        for expected in (200, 400):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post(reverse('sale-void', args=[sale['id']])).status_code, expected)
        reverse_event = OutboundEvent.objects.get(event_type='loyalty.reverse')
        self.assertEqual(reverse_event.payload['data'], {'order_reference': f'iq-sale-{ref}'})
        self.assertEqual(self.dispatch.call_count, 2)

    def test_void_without_card_emits_nothing(self):
        session, product = self.open(), self.stocked()
        sale = self.sell(session['id'], [(product, 1)], method='card').json()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('sale-void', args=[sale['id']]))
        self.assertFalse(OutboundEvent.objects.exists())


@mock.patch('sync.loyalty.requests.post')
class LookupTests(LoyaltyTestCase):
    def test_lookup_returns_customer(self, post):
        post.return_value = reply(200, {**CUSTOMER, 'surname': 'Khan'})
        response = self.lookup()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), CUSTOMER)
        (url,), kwargs = post.call_args
        self.assertEqual(url, BASE + 'rewards/api/integration/lookup/')
        self.assertEqual(kwargs['timeout'], 1.5)
        self.assertFalse(kwargs['allow_redirects'])
        self.assertTrue(verify(SECRET, kwargs['data'], kwargs['headers']['X-Integration-Signature']))
        self.assertEqual(json.loads(kwargs['data']), {'barcode': CARD})

    def test_lookup_timeout_returns_503_fast(self, post):
        for failure in (requests.Timeout(), requests.ConnectionError()):
            cache.clear()
            post.side_effect = failure
            response = self.lookup()
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()['code'], 'loyalty_unavailable')
        post.side_effect = None
        for status_code in (500, 401):  # m18 broken, or the secrets don't match
            post.return_value = reply(status_code)
            self.assertEqual(self.lookup().status_code, 503)

    def test_lookup_unknown_404(self, post):
        post.return_value = reply(404, {'detail': 'not_found'})
        response = self.lookup()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'not_found')

    def test_lookup_hit_is_cached(self, post):
        post.return_value = reply(200, CUSTOMER)
        self.assertEqual(self.lookup().json(), CUSTOMER)
        self.assertEqual(self.lookup().json(), CUSTOMER)
        self.assertEqual(post.call_count, 1)
        self.assertFalse(any(CARD in key for key in cache._cache))  # card numbers are not cache keys

    def test_lookup_misses_are_not_cached(self, post):
        post.return_value = reply(404)
        self.lookup()
        self.lookup()
        self.assertEqual(post.call_count, 2)

    def test_lookup_other_company_uses_own_connection(self, post):
        post.return_value = reply(200, CUSTOMER)
        self.lookup()
        other = CompanyFactory()
        StorefrontConnection.objects.create(company=other, base_url='https://other.example.com/', webhook_secret='other')
        response = self.lookup(client=self.as_user(UserFactory(company=other)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(post.call_count, 2)  # the first company's cached hit is not shared
        (url,), kwargs = post.call_args
        self.assertEqual(url, 'https://other.example.com/rewards/api/integration/lookup/')
        self.assertTrue(verify('other', kwargs['data'], kwargs['headers']['X-Integration-Signature']))

    def test_lookup_not_configured_409(self, post):
        self.connection.delete()
        response = self.lookup()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'not_configured')
        post.assert_not_called()

    def test_lookup_requires_a_barcode(self, post):
        self.assertEqual(self.lookup(barcode='').status_code, 400)
        self.assertEqual(APIClient().post(reverse('loyalty-lookup'), {'barcode': CARD}).status_code, 401)
        post.assert_not_called()

    def test_till_operator_can_lookup(self, post):
        post.return_value = reply(200, CUSTOMER)
        self.enterContext(mock.patch.object(ScopedRateThrottle, 'THROTTLE_RATES', {'code_login': '1000/min'}))
        admin = self.as_user(self.manager)
        for role, expected in ((TILL, 200), (DEMAND, 403)):
            code = admin.post(reverse('operator-list'), {'name': role, 'role': role}).json()['code']
            token = APIClient().post(reverse('code-login'), {'code': code}, format='json').json()['access_token']
            operator = APIClient()
            operator.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
            self.assertEqual(self.lookup(client=operator).status_code, expected, role)


@mock.patch('sync.tasks.requests.post')
class DeliveryTests(LoyaltyTestCase):
    def emit_award(self):
        session, product = self.open(), self.stocked()
        self.sell_with_card(session['id'], product)
        return OutboundEvent.objects.get()

    def test_delivery_is_signed_and_marked_delivered(self, post):
        post.return_value = reply(200, {'status': 'applied'})
        event = self.emit_award()
        self.assertTrue(deliver_outbound_event(event.pk))
        (url,), kwargs = post.call_args
        self.assertEqual(url, BASE + 'rewards/api/integration/events/')
        self.assertEqual((kwargs['timeout'], kwargs['allow_redirects']), (5, False))
        self.assertTrue(verify(SECRET, kwargs['data'], kwargs['headers']['X-Integration-Signature']))
        self.assertEqual(json.loads(kwargs['data']), event.payload)
        event.refresh_from_db()
        self.assertEqual(event.status, 'delivered')
        self.assertFalse(deliver_outbound_event(event.pk))  # never resent
        self.assertEqual(post.call_count, 1)

    def test_delivery_backoff_and_failed_after_20(self, post):
        post.return_value = reply(503)
        event = self.emit_award()
        before = timezone.now()
        self.assertFalse(deliver_outbound_event(event.pk))
        event.refresh_from_db()
        self.assertEqual((event.status, event.attempts), ('pending', 1))
        self.assertGreaterEqual(event.next_attempt_at, before + timedelta(seconds=2))
        self.assertIn('HTTP 503', event.last_error)
        self.assertFalse(deliver_outbound_event(event.pk))  # not due yet: no request
        self.assertEqual(post.call_count, 1)

        post.side_effect = requests.Timeout()
        for _ in range(19):
            OutboundEvent.objects.filter(pk=event.pk).update(next_attempt_at=timezone.now())
            deliver_outbound_event(event.pk)
        event.refresh_from_db()
        self.assertEqual((event.status, event.attempts), ('failed', 20))
        self.assertNotIn(SECRET, event.last_error)

    def test_sweep_sends_only_due_pending_events(self, post):
        post.return_value = reply(200)
        due = self.emit_award()
        later = OutboundEvent.objects.create(
            connection=self.connection, event_type='loyalty.reverse', payload={},
            next_attempt_at=timezone.now() + timedelta(hours=1),
        )
        self.assertEqual(deliver_outbound_events(), 1)
        due.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual((due.status, later.status), ('delivered', 'pending'))

    def test_inactive_connection_is_not_swept(self, post):
        event = self.emit_award()
        StorefrontConnection.objects.filter(pk=self.connection.pk).update(is_active=False)
        self.assertEqual(deliver_outbound_events(), 0)
        self.assertFalse(deliver_outbound_event(event.pk))
        post.assert_not_called()
