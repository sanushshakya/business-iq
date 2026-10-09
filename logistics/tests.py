# logistics/tests.py

from unittest import mock

import requests
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, UserFactory
from common.models import FreightRateCache
from logistics.models import AlternativeSupplier, FreightAlert, UserSupplier
from logistics.tasks import check_freight_rates


class FreightAlertApiTests(TestCase):
    def test_dismiss(self):
        client = APIClient()
        user = UserFactory()
        client.force_authenticate(user)
        alert = FreightAlert.objects.create(
            company=user.company, shipping_lane='CN-UK', current_rate=110, baseline_rate=100, change_percent=10
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


class SupplierApiTests(TestCase):
    def setUp(self):
        self.user = UserFactory()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse('usersupplier-list')

    def payload(self, **overrides):
        data = {'name': 'Acme Dates', 'country_of_origin': 'Saudi Arabia', 'product_categories': ['Dried fruit'], 'lead_time_days': 21}
        data.update(overrides)
        return data

    def test_user_supplier_crud(self):
        response = self.client.post(self.url, self.payload(notes='Preferred'), format='json')
        self.assertEqual(response.status_code, 201, response.content)
        pk = response.json()['id']
        self.assertEqual(response.json()['company'], self.user.company.pk)

        response = self.client.patch(reverse('usersupplier-detail', args=[pk]), {'lead_time_days': 14}, format='json')
        self.assertEqual(response.json()['lead_time_days'], 14)
        self.assertEqual(self.client.delete(reverse('usersupplier-detail', args=[pk])).status_code, 204)

    def test_categories_must_be_a_list_of_names(self):
        for bad in ('Dried fruit', [1, 2], [''], {'a': 1}):
            response = self.client.post(self.url, self.payload(product_categories=bad), format='json')
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn('product_categories', response.json())

    def test_names_are_unique_per_company_not_globally(self):
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 201)
        response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.json())

        other = UserFactory()
        other_client = APIClient()
        other_client.force_authenticate(other)
        self.assertEqual(other_client.post(self.url, self.payload(), format='json').status_code, 201)

    def test_suppliers_are_private_to_their_company(self):
        mine = UserSupplier.objects.create(company=self.user.company, **{**self.payload(), 'name': 'Mine'})
        theirs = UserSupplier.objects.create(company=CompanyFactory(), **{**self.payload(), 'name': 'Theirs'})
        names = [s['name'] for s in self.client.get(self.url).json()['results']]
        self.assertEqual(names, ['Mine'])
        self.assertEqual(self.client.get(reverse('usersupplier-detail', args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('usersupplier-detail', args=[mine.pk])).status_code, 200)

    def test_alternative_suppliers_are_read_only_and_scoped(self):
        url = reverse('alternativesupplier-list')
        data = {'name': 'Backup Dates', 'country_of_origin': 'Tunisia', 'product_categories': ['Dried fruit'], 'lead_time_days': 30}
        AlternativeSupplier.objects.create(company=self.user.company, **data)
        AlternativeSupplier.objects.create(company=CompanyFactory(), **{**data, 'name': 'Not mine'})
        self.assertEqual([s['name'] for s in self.client.get(url).json()['results']], ['Backup Dates'])
        self.assertEqual(self.client.post(url, data, format='json').status_code, 405)
        detail = reverse('alternativesupplier-detail', args=[AlternativeSupplier.objects.get(name='Backup Dates').pk])
        self.assertEqual(self.client.delete(detail).status_code, 405)
        self.assertEqual(self.client.patch(detail, {'name': 'x'}, format='json').status_code, 405)

    def test_freight_rates_are_read_only_and_scoped(self):
        FreightRateCache.objects.create(company=self.user.company, service_code='CN-UK', rate=1200, currency='USD')
        FreightRateCache.objects.create(company=CompanyFactory(), service_code='IN-UK', rate=900)
        url = reverse('freightrate-list')
        self.assertEqual([r['service_code'] for r in self.client.get(url).json()['results']], ['CN-UK'])
        self.assertEqual(self.client.post(url, {'service_code': 'X', 'rate': 1}, format='json').status_code, 405)
