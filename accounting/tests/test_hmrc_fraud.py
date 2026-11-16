"""G56: HMRC fraud-prevention headers for WEB_APP_VIA_SERVER (spec v3.3)."""

import re

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from accounting.hmrc import fraud
from authentication.models import TwoFactorDevice
from common.tests.factories import UserFactory

CLIENT = {
    'device_id': 'beec798b-b366-47fa-b1f8-92cede14a1ce',
    'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)',
    'screens': [{'width': 1920, 'height': 1080, 'scaling_factor': '1.25', 'colour_depth': 24}],
    'window': {'width': 1256, 'height': 803},
    'timezone': 'UTC+01:00',
    'web_version': 'abc123',
}

HEADERS = ['Gov-Client-Connection-Method', 'Gov-Client-Browser-JS-User-Agent', 'Gov-Client-Device-ID',
           'Gov-Client-Multi-Factor', 'Gov-Client-Public-IP', 'Gov-Client-Public-IP-Timestamp',
           'Gov-Client-Public-Port', 'Gov-Client-Screens', 'Gov-Client-Timezone', 'Gov-Client-User-IDs',
           'Gov-Client-Window-Size', 'Gov-Vendor-Forwarded', 'Gov-Vendor-License-IDs', 'Gov-Vendor-Product-Name',
           'Gov-Vendor-Public-IP', 'Gov-Vendor-Version']


def valid_client():
    serializer = fraud.ClientInfoSerializer(data=CLIENT)
    assert serializer.is_valid(), serializer.errors
    return serializer.validated_data


@override_settings(HMRC_VENDOR_PUBLIC_IP='203.0.113.6', HMRC_VENDOR_PRODUCT_NAME='M18 World Foods IQ',
                   APP_VERSION='dev')
class FraudHeaderTests(TestCase):
    def setUp(self):
        self.user = UserFactory()
        self.factory = RequestFactory()

    def headers(self, **meta):
        request = self.factory.post('/accounting/hmrc/returns/', **meta)
        return fraud.headers(request, valid_client(), self.user)

    def test_every_web_app_via_server_header_is_sent(self):
        self.assertEqual(sorted(self.headers(HTTP_CLOUDFRONT_VIEWER_ADDRESS='81.2.69.142:52311')), sorted(HEADERS))

    def test_screens_and_window_are_percent_encoded_key_values(self):
        headers = self.headers()
        self.assertEqual(headers['Gov-Client-Screens'], 'width=1920&height=1080&scaling-factor=1.25&colour-depth=24')
        self.assertEqual(headers['Gov-Client-Window-Size'], 'width=1256&height=803')
        self.assertEqual(headers['Gov-Client-Timezone'], 'UTC+01:00')
        self.assertEqual(headers['Gov-Client-Device-ID'], CLIENT['device_id'])
        self.assertEqual(headers['Gov-Client-Browser-JS-User-Agent'], CLIENT['user_agent'])

    def test_public_ip_and_port_from_cloudfront_viewer_address(self):
        headers = self.headers(HTTP_CLOUDFRONT_VIEWER_ADDRESS='81.2.69.142:52311')
        self.assertEqual(headers['Gov-Client-Public-IP'], '81.2.69.142')
        self.assertEqual(headers['Gov-Client-Public-Port'], '52311')
        self.assertRegex(headers['Gov-Client-Public-IP-Timestamp'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$')
        headers = self.headers(HTTP_CLOUDFRONT_VIEWER_ADDRESS='[2001:4860:4860::8888]:443')
        self.assertEqual(headers['Gov-Client-Public-IP'], '2001:4860:4860::8888')
        self.assertEqual(headers['Gov-Client-Public-Port'], '')  # a server port is never sent

    def test_private_address_is_sent_empty(self):
        headers = self.headers(REMOTE_ADDR='10.0.0.5')
        self.assertEqual(headers['Gov-Client-Public-IP'], '')
        self.assertEqual(headers['Gov-Client-Public-Port'], '')
        self.assertEqual(headers['Gov-Client-Public-IP-Timestamp'], '')
        self.assertEqual(headers['Gov-Vendor-Forwarded'], '')

    def test_multi_factor_from_last_totp(self):
        self.assertEqual(self.headers()['Gov-Client-Multi-Factor'], '')
        TwoFactorDevice.objects.create(user=self.user, secret='x', confirmed_at=timezone.now(),
                                       last_used_step=56340000)
        self.user.refresh_from_db()
        value = self.headers()['Gov-Client-Multi-Factor']
        self.assertRegex(value, r'^type=TOTP&timestamp=2023-07-24T12%3A00Z&unique-reference=[0-9a-f]{64}$')

    def test_user_ids_never_carry_the_email(self):
        value = self.headers()['Gov-Client-User-IDs']
        self.assertEqual(value, f'm18-iq={self.user.pk}')
        self.assertNotIn('example', value)

    def test_vendor_headers(self):
        headers = self.headers(HTTP_CLOUDFRONT_VIEWER_ADDRESS='81.2.69.142:52311')
        self.assertEqual(headers['Gov-Client-Connection-Method'], 'WEB_APP_VIA_SERVER')
        self.assertEqual(headers['Gov-Vendor-Version'], 'm18-iq=dev&m18-iq-web=abc123')
        self.assertEqual(headers['Gov-Vendor-Product-Name'], 'M18%20World%20Foods%20IQ')
        self.assertEqual(headers['Gov-Vendor-Public-IP'], '203.0.113.6')
        self.assertEqual(headers['Gov-Vendor-Forwarded'], 'by=203.0.113.6&for=81.2.69.142')
        self.assertEqual(headers['Gov-Vendor-License-IDs'], '')

    def test_values_are_ascii(self):
        client = dict(CLIENT, user_agent='Navigateur é')
        serializer = fraud.ClientInfoSerializer(data=client)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        request = self.factory.post('/')
        value = fraud.headers(request, serializer.validated_data, self.user)['Gov-Client-Browser-JS-User-Agent']
        self.assertTrue(value.isascii())
        self.assertTrue(re.search('%C3%A9', value))

    def test_bad_client_fields_are_named(self):
        serializer = fraud.ClientInfoSerializer(data=dict(CLIENT, timezone='GMT', screens=[]))
        self.assertFalse(serializer.is_valid())
        self.assertIn('timezone', serializer.errors)
        self.assertIn('screens', serializer.errors)
        serializer = fraud.ClientInfoSerializer(data=dict(CLIENT, device_id='not-a-uuid'))
        self.assertFalse(serializer.is_valid())
        self.assertIn('device_id', serializer.errors)
