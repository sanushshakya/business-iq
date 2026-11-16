"""G56: connecting a company to HMRC (OAuth) over the API: connect, callback, status, disconnect."""

from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.hmrc import client
from accounting.models import HmrcAuthState, HmrcConnection
from accounting.tests.test_hmrc_client import SANDBOX, TOKENS, answer
from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK


@override_settings(**SANDBOX)
class HmrcOAuthTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory(name='Test Foods Ltd', vat_number='GB123456789')
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.api = APIClient()
        self.api.force_authenticate(self.staff)

    def connect(self, api=None):
        response = (api or self.api).post(reverse('hmrc-connect'))
        self.assertEqual(response.status_code, 200, response.content)
        return parse_qs(urlparse(response.json()['authorize_url']).query)['state'][0]

    def callback(self, state, code='the-code', tokens=TOKENS, **extra):
        with mock.patch.object(client.SESSION, 'post', return_value=answer(200, tokens)) as post:
            response = self.client.get(reverse('hmrc-callback'), {'code': code, 'state': state, **extra})
        return response, post

    def test_connect_returns_hmrc_authorize_url(self):
        response = self.api.post(reverse('hmrc-connect'))
        url = urlparse(response.json()['authorize_url'])
        self.assertEqual(f'{url.scheme}://{url.netloc}{url.path}', 'https://test-api.service.hmrc.gov.uk/oauth/authorize')
        query = parse_qs(url.query)
        self.assertEqual(query['response_type'], ['code'])
        self.assertEqual(query['client_id'], ['test-client-id'])
        self.assertEqual(query['scope'], ['read:vat write:vat'])
        self.assertEqual(query['redirect_uri'], [SANDBOX['HMRC_REDIRECT_URI']])
        self.assertGreaterEqual(len(query['state'][0]), 40)
        state = HmrcAuthState.objects.get()
        self.assertNotEqual(state.state_hash, query['state'][0])  # only a hash is kept
        self.assertEqual((state.company, state.user), (self.company, self.staff))

    def test_connect_needs_vat_number_and_environment(self):
        self.company.vat_number = ''
        self.company.save()
        response = self.api.post(reverse('hmrc-connect'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('VAT number', str(response.json()))
        with override_settings(HMRC_VAT_ENVIRONMENT='off', HMRC_BASE_URL=''):
            self.assertEqual(self.api.post(reverse('hmrc-connect')).status_code, 400)

    def test_callback_stores_connection_and_redirects(self):
        state = self.connect()
        response, post = self.callback(state)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/dashboard/accounts?hmrc=connected')
        self.assertEqual(post.call_args.kwargs['data']['code'], 'the-code')
        connection = HmrcConnection.objects.get()
        self.assertEqual((connection.company, connection.vrn, connection.environment, connection.connected_by),
                         (self.company, '123456789', 'sandbox', self.staff))
        self.assertEqual(connection.access_token, 'new-access-token-1')
        self.assertFalse(HmrcAuthState.objects.exists())

    def test_callback_reconnect_replaces_tokens(self):
        self.callback(self.connect())
        self.callback(self.connect(), tokens=dict(TOKENS, access_token='second-access-token'))
        self.assertEqual(HmrcConnection.objects.get().access_token, 'second-access-token')

    def test_callback_vrn_is_first_nine_digits(self):
        self.company.vat_number = 'GB123456789001'
        self.company.save()
        self.callback(self.connect())
        self.assertEqual(HmrcConnection.objects.get().vrn, '123456789')

    def test_callback_state_is_single_use_and_expires(self):
        state = self.connect()
        self.callback(state)
        HmrcConnection.objects.all().delete()
        response, post = self.callback(state)  # replayed
        self.assertEqual(response['Location'], '/dashboard/accounts?hmrc=error&reason=state')
        post.assert_not_called()
        old = self.connect()
        HmrcAuthState.objects.update(created_at=timezone.now() - timedelta(minutes=11))
        response, post = self.callback(old)
        self.assertEqual(response['Location'], '/dashboard/accounts?hmrc=error&reason=state')
        self.assertFalse(HmrcAuthState.objects.exists())  # expired ones are cleared
        response, _ = self.callback('made-up-state')
        self.assertEqual(response['Location'], '/dashboard/accounts?hmrc=error&reason=state')
        self.assertFalse(HmrcConnection.objects.exists())

    def test_callback_error_param_is_denied(self):
        state = self.connect()
        with mock.patch.object(client.SESSION, 'post') as post:
            response = self.client.get(reverse('hmrc-callback'), {'error': 'access_denied', 'state': state})
        self.assertEqual(response['Location'], '/dashboard/accounts?hmrc=error&reason=denied')
        post.assert_not_called()
        self.assertFalse(HmrcAuthState.objects.exists())

    def test_callback_refused_exchange(self):
        state = self.connect()
        with mock.patch.object(client.SESSION, 'post', return_value=answer(400, {'error': 'invalid_grant'})):
            response = self.client.get(reverse('hmrc-callback'), {'code': 'x', 'state': state})
        self.assertEqual(response['Location'], '/dashboard/accounts?hmrc=error&reason=exchange')
        self.assertFalse(HmrcConnection.objects.exists())

    def test_other_company_cannot_use_my_state(self):
        state = self.connect()
        other = CompanyFactory(vat_number='GB987654321')
        HmrcAuthState.objects.update(company=other)  # what a forged state would need
        self.callback(state)
        self.assertEqual(HmrcConnection.objects.get().company, other)
        self.assertFalse(HmrcConnection.objects.filter(company=self.company).exists())

    def test_status_never_shows_tokens(self):
        self.callback(self.connect())
        body = self.api.get(reverse('hmrc-status')).json()
        self.assertEqual(body['environment'], 'sandbox')
        self.assertTrue(body['connected'])
        self.assertEqual(body['vrn'], '123456789')
        self.assertNotIn('token', str(body))

    def test_members_read_status_staff_connect_operators_403(self):
        member = APIClient()
        member.force_authenticate(UserFactory(company=self.company))
        self.assertEqual(member.get(reverse('hmrc-status')).json(),
                         {'environment': 'sandbox', 'connected': False, 'vrn': None, 'connected_at': None})
        self.assertEqual(member.post(reverse('hmrc-connect')).status_code, 403)
        self.assertEqual(member.delete(reverse('hmrc-connection')).status_code, 403)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        picker = APIClient()
        picker.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(picker.get(reverse('hmrc-status')).status_code, 403)
        self.assertEqual(picker.post(reverse('hmrc-connect')).status_code, 403)

    def test_disconnect_deletes_tokens(self):
        self.callback(self.connect())
        self.assertEqual(self.api.delete(reverse('hmrc-connection')).status_code, 204)
        self.assertFalse(HmrcConnection.objects.exists())
        other = CompanyFactory()
        HmrcConnection.objects.create(company=other, vrn='987654321', environment='sandbox', access_token='a',
                                      refresh_token='r', access_expires_at=timezone.now(),
                                      refresh_expires_at=timezone.now(), scope='read:vat')
        self.api.delete(reverse('hmrc-connection'))
        self.assertTrue(HmrcConnection.objects.filter(company=other).exists())
