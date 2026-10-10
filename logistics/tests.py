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

    def test_alerts_cannot_be_written_by_clients(self):
        client = APIClient()
        user = UserFactory()
        client.force_authenticate(user)
        alert = FreightAlert.objects.create(
            company=user.company, shipping_lane='CN-UK', current_rate=110, baseline_rate=100, change_percent=10
        )
        payload = {'shipping_lane': 'X', 'current_rate': '1', 'baseline_rate': '1', 'change_percent': 0}
        self.assertEqual(client.post(reverse('freightalert-list'), payload).status_code, 405)
        self.assertEqual(client.patch(reverse('freightalert-detail', args=[alert.pk]), payload).status_code, 405)
        self.assertEqual(client.delete(reverse('freightalert-detail', args=[alert.pk])).status_code, 405)


@override_settings(FREIGHT_RATES_API_URL='https://rates.test', RATE_CHANGE_THRESHOLD=5.0)
class CheckFreightRatesTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()

    def run_task(self, rate, lane='CN-UK', **extra):
        payload = [{'company_id': self.company.pk, 'shipping_lane': lane, 'current_rate': rate, **extra}]
        with mock.patch('logistics.tasks.requests.get') as get:
            get.return_value.json.return_value = payload
            return check_freight_rates()

    def baseline(self, lane='CN-UK'):
        return FreightRateCache.objects.get(company=self.company, service_code=lane)

    def test_the_first_sighting_only_records_a_baseline(self):
        self.assertEqual(self.run_task(100, currency='USD'), 0)
        self.assertEqual((self.baseline().rate, self.baseline().currency), (100, 'USD'))
        self.assertFalse(FreightAlert.objects.exists())

    def test_a_small_change_neither_alerts_nor_moves_the_baseline(self):
        self.run_task(100)
        self.assertEqual(self.run_task(102), 0)
        self.assertEqual(self.baseline().rate, 100)

    def test_a_big_change_alerts_and_becomes_the_new_baseline(self):
        self.run_task(100)
        self.assertEqual(self.run_task(110), 1)
        alert = FreightAlert.objects.get()
        self.assertEqual((alert.baseline_rate, alert.current_rate, alert.shipping_lane), (100, 110, 'CN-UK'))
        self.assertAlmostEqual(alert.change_percent, 10.0)
        self.assertEqual(self.baseline().rate, 110)
        self.assertEqual(self.run_task(111), 0)  # measured from the new baseline now

    def test_a_slow_drift_still_alerts_once_it_adds_up(self):
        self.run_task(100)
        self.assertEqual(self.run_task(103), 0)
        self.assertEqual(self.run_task(106), 1)  # 6% above the original baseline

    def test_falls_count_too(self):
        self.run_task(100)
        self.assertEqual(self.run_task(90), 1)
        self.assertAlmostEqual(FreightAlert.objects.get().change_percent, -10.0)

    def test_services_and_companies_are_tracked_separately(self):
        self.run_task(100, lane='CN-UK')
        self.run_task(100, lane='IN-UK')
        self.assertEqual(self.run_task(120, lane='CN-UK'), 1)
        self.assertEqual(self.baseline('IN-UK').rate, 100)

    def test_api_failure_is_handled(self):
        with mock.patch('logistics.tasks.requests.get', side_effect=requests.ConnectionError):
            self.assertEqual(check_freight_rates(), 0)

    def test_bad_entries_are_skipped_without_losing_the_good_ones(self):
        payload = [
            {'company_id': 999999, 'shipping_lane': 'CN-UK', 'current_rate': 100},   # unknown company
            {'shipping_lane': 'CN-UK', 'current_rate': 100},                          # missing company
            {'company_id': self.company.pk, 'shipping_lane': 'CN-UK', 'current_rate': 'abc'},
            {'company_id': self.company.pk, 'shipping_lane': 'CN-UK', 'current_rate': -5},
            {'company_id': self.company.pk, 'shipping_lane': 'IN-UK', 'current_rate': 100},
        ]
        with mock.patch('logistics.tasks.requests.get') as get:
            get.return_value.json.return_value = payload
            self.assertEqual(check_freight_rates(), 0)
        self.assertEqual(FreightRateCache.objects.count(), 1)
        self.assertEqual(self.baseline('IN-UK').rate, 100)

    def test_a_payload_that_is_not_a_list_is_ignored(self):
        with mock.patch('logistics.tasks.requests.get') as get:
            get.return_value.json.return_value = {'error': 'nope'}
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
