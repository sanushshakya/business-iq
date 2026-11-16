"""G56: the HMRC HTTP client: token exchange and refresh, errors, and nothing secret in logs."""

import logging
from datetime import timedelta
from unittest import mock

import requests
from django.test import TestCase, override_settings
from django.utils import timezone

from accounting.hmrc import client
from accounting.models import HmrcConnection
from common.tests.factories import CompanyFactory

SANDBOX = dict(HMRC_VAT_ENVIRONMENT='sandbox', HMRC_BASE_URL='https://test-api.service.hmrc.gov.uk',
               HMRC_CLIENT_ID='test-client-id', HMRC_CLIENT_SECRET='test-client-secret-value',
               HMRC_REDIRECT_URI='http://localhost:8000/accounting/hmrc/callback/', HMRC_TIMEOUT_SECONDS=20)
FRAUD = {'Gov-Client-Connection-Method': 'WEB_APP_VIA_SERVER'}


def answer(status, body=None):
    response = mock.Mock(spec=requests.Response)
    response.status_code = status
    response.ok = status < 400
    response.json.return_value = body if body is not None else {}
    response.text = '' if body is None else str(body)
    return response


TOKENS = {'access_token': 'new-access-token-1', 'refresh_token': 'new-refresh-token-1', 'expires_in': 14400,
          'scope': 'read:vat write:vat', 'token_type': 'bearer'}


@override_settings(**SANDBOX)
class HmrcClientTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory(name='Test Foods Ltd')

    def connect(self, expires_in=timedelta(hours=4)):
        return HmrcConnection.objects.create(
            company=self.company, vrn='123456789', environment='sandbox', access_token='old-access-token',
            refresh_token='old-refresh-token', access_expires_at=timezone.now() + expires_in,
            refresh_expires_at=timezone.now() + timedelta(days=500), scope='read:vat write:vat')

    def test_exchange_code_sends_secret_in_body_not_url(self):
        with mock.patch.object(client.SESSION, 'post', return_value=answer(200, TOKENS)) as post:
            self.assertEqual(client.exchange_code('the-code')['access_token'], 'new-access-token-1')
        url = post.call_args.args[0]
        self.assertEqual(url, 'https://test-api.service.hmrc.gov.uk/oauth/token')
        self.assertNotIn('secret', url)
        data = post.call_args.kwargs['data']
        self.assertEqual(data['client_secret'], 'test-client-secret-value')
        self.assertEqual(data['grant_type'], 'authorization_code')
        self.assertEqual(data['code'], 'the-code')
        self.assertEqual(data['redirect_uri'], SANDBOX['HMRC_REDIRECT_URI'])
        self.assertEqual(post.call_args.kwargs['timeout'], 20)

    def test_access_token_used_while_fresh(self):
        connection = self.connect()
        with mock.patch.object(client.SESSION, 'post') as post:
            self.assertEqual(client.access_token(connection), 'old-access-token')
        post.assert_not_called()

    def test_access_token_refreshes_when_nearly_expired(self):
        connection = self.connect(expires_in=timedelta(seconds=30))
        with mock.patch.object(client.SESSION, 'post', return_value=answer(200, TOKENS)) as post:
            self.assertEqual(client.access_token(connection), 'new-access-token-1')
        self.assertEqual(post.call_args.kwargs['data']['grant_type'], 'refresh_token')
        self.assertEqual(post.call_args.kwargs['data']['refresh_token'], 'old-refresh-token')
        stored = HmrcConnection.objects.get()
        self.assertEqual(stored.refresh_token, 'new-refresh-token-1')
        self.assertGreater(stored.access_expires_at, timezone.now() + timedelta(hours=3))

    def test_refused_refresh_deletes_connection(self):
        connection = self.connect(expires_in=timedelta(seconds=-5))
        with mock.patch.object(client.SESSION, 'post', return_value=answer(400, {'error': 'invalid_grant'})):
            with self.assertRaises(client.HmrcError) as raised:
                client.access_token(connection)
        self.assertEqual(raised.exception.code, 'RECONNECT')
        self.assertEqual(raised.exception.message, 'Connect to HMRC again.')
        self.assertFalse(HmrcConnection.objects.exists())

    def test_call_sends_bearer_accept_and_fraud_headers(self):
        connection = self.connect()
        with mock.patch.object(client.SESSION, 'request', return_value=answer(200, {'obligations': []})) as request:
            client.call(connection, 'GET', '/organisations/vat/123456789/obligations', fraud=FRAUD,
                        params={'status': 'O'})
        method, url = request.call_args.args
        self.assertEqual((method, url),
                         ('GET', 'https://test-api.service.hmrc.gov.uk/organisations/vat/123456789/obligations'))
        sent = request.call_args.kwargs['headers']
        self.assertEqual(sent['Authorization'], 'Bearer old-access-token')
        self.assertEqual(sent['Accept'], 'application/vnd.hmrc.1.0+json')
        self.assertEqual(sent['Gov-Client-Connection-Method'], 'WEB_APP_VIA_SERVER')
        self.assertNotIn('Gov-Test-Scenario', sent)

    def test_call_parses_hmrc_error(self):
        connection = self.connect()
        body = {'code': 'VRN_INVALID', 'message': 'The provided VRN is invalid'}
        with mock.patch.object(client.SESSION, 'request', return_value=answer(400, body)):
            with self.assertRaises(client.HmrcError) as raised:
                client.call(connection, 'GET', '/x', fraud=FRAUD)
        self.assertEqual((raised.exception.status, raised.exception.code), (400, 'VRN_INVALID'))
        self.assertEqual(raised.exception.message, 'The provided VRN is invalid')

    def test_call_reads_first_of_several_errors(self):
        connection = self.connect()
        body = {'code': 'INVALID_REQUEST', 'message': 'Invalid request',
                'errors': [{'code': 'INVALID_MONETARY_AMOUNT', 'message': 'amount should be a monetary value'}]}
        with mock.patch.object(client.SESSION, 'request', return_value=answer(400, body)):
            with self.assertRaises(client.HmrcError) as raised:
                client.call(connection, 'POST', '/x', fraud=FRAUD, json={})
        self.assertEqual(raised.exception.code, 'INVALID_MONETARY_AMOUNT')

    def test_timeout_is_no_answer(self):
        connection = self.connect()
        with mock.patch.object(client.SESSION, 'request', side_effect=requests.Timeout('slow')):
            with self.assertRaises(client.HmrcError) as raised:
                client.call(connection, 'GET', '/x', fraud=FRAUD)
        self.assertEqual((raised.exception.status, raised.exception.code), (None, 'NO_ANSWER'))

    def test_test_scenario_only_in_sandbox(self):
        connection = self.connect()
        with mock.patch.object(client.SESSION, 'request', return_value=answer(200, {})) as request:
            client.call(connection, 'GET', '/x', fraud=FRAUD, test_scenario='QUARTERLY_NONE_MET')
        self.assertEqual(request.call_args.kwargs['headers']['Gov-Test-Scenario'], 'QUARTERLY_NONE_MET')
        with override_settings(HMRC_VAT_ENVIRONMENT='production', HMRC_BASE_URL='https://api.service.hmrc.gov.uk'):
            HmrcConnection.objects.filter(pk=connection.pk).update(environment='production')
            connection.refresh_from_db()
            with mock.patch.object(client.SESSION, 'request', return_value=answer(200, {})) as request:
                client.call(connection, 'GET', '/x', fraud=FRAUD, test_scenario='QUARTERLY_NONE_MET')
            self.assertNotIn('Gov-Test-Scenario', request.call_args.kwargs['headers'])

    def test_tokens_from_another_environment_are_not_used(self):
        connection = self.connect()
        with override_settings(HMRC_VAT_ENVIRONMENT='production', HMRC_BASE_URL='https://api.service.hmrc.gov.uk'):
            with mock.patch.object(client.SESSION, 'request') as request:
                with self.assertRaises(client.HmrcError) as raised:
                    client.call(connection, 'GET', '/x', fraud=FRAUD)
        request.assert_not_called()
        self.assertEqual(raised.exception.code, 'RECONNECT')

    def test_nothing_secret_is_logged(self):
        connection = self.connect(expires_in=timedelta(seconds=-5))
        secrets = ['old-access-token', 'old-refresh-token', 'test-client-secret-value', 'new-access-token-1',
                   'the-code']
        with self.assertLogs('accounting.hmrc', logging.DEBUG) as logs:
            logging.getLogger('accounting.hmrc').debug('start')
            with mock.patch.object(client.SESSION, 'post', return_value=answer(200, TOKENS)):
                client.access_token(connection)
            with mock.patch.object(client.SESSION, 'request',
                                   return_value=answer(500, {'code': 'SERVER_ERROR', 'message': 'x'})):
                with self.assertRaises(client.HmrcError):
                    client.call(HmrcConnection.objects.get(), 'GET', '/x', fraud=FRAUD)
            with mock.patch.object(client.SESSION, 'post', return_value=answer(400, {'error': 'invalid_grant'})):
                with self.assertRaises(client.HmrcError):
                    client.exchange_code('the-code')
        text = '\n'.join(logs.output)
        for secret in secrets:
            self.assertNotIn(secret, text)
