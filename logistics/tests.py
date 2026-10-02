# logistics/tests.py

from unittest import mock

import requests
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, UserFactory
from logistics.models import FreightAlert
from logistics.tasks import check_freight_rates


class FreightAlertApiTests(TestCase):
    def test_dismiss(self):
        client = APIClient()
        client.force_authenticate(UserFactory())
        alert = FreightAlert.objects.create(
            company=CompanyFactory(), shipping_lane='CN-UK', current_rate=110, baseline_rate=100, change_percent=10
        )
        response = client.post(reverse('freightalert-dismiss', args=[alert.pk]))
        self.assertEqual(response.status_code, 200)
        alert.refresh_from_db()
        self.assertTrue(alert.is_dismissed)


@override_settings(FREIGHT_RATES_API_URL='https://rates.test', RATE_CHANGE_THRESHOLD=5.0)
class CheckFreightRatesTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        FreightAlert.objects.create(
            company=self.company, shipping_lane='CN-UK', current_rate=100, baseline_rate=100, change_percent=0
        )

    def run_task(self, rate):
        payload = [{'company_id': self.company.pk, 'shipping_lane': 'CN-UK', 'current_rate': rate}]
        with mock.patch('logistics.tasks.requests.get') as get:
            get.return_value.json.return_value = payload
            return check_freight_rates()

    def test_alert_created_when_change_exceeds_threshold(self):
        self.assertEqual(self.run_task(110), 1)
        latest = FreightAlert.objects.order_by('-alert_date', '-id').first()
        self.assertEqual(latest.baseline_rate, 100)
        self.assertAlmostEqual(latest.change_percent, 10.0)

    def test_no_alert_for_small_change(self):
        self.assertEqual(self.run_task(102), 0)

    def test_api_failure_is_handled(self):
        with mock.patch('logistics.tasks.requests.get', side_effect=requests.ConnectionError):
            self.assertEqual(check_freight_rates(), 0)

    @override_settings(FREIGHT_RATES_API_URL='')
    def test_skips_when_unconfigured(self):
        self.assertEqual(check_freight_rates(), 0)
