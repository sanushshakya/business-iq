# common/tests/test_company_settings.py

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.models import Setting

from .factories import CompanyFactory, UserFactory


class SettingApiTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.member = UserFactory(company=self.company)
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.url = reverse('setting-list')

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_members_can_read_but_not_change(self):
        Setting.objects.create(company=self.company, key='default_margin_percent', value='25')
        client = self.as_user(self.member)
        self.assertEqual(client.get(self.url).json()['results'][0]['value'], '25')
        self.assertEqual(client.post(self.url, {'key': 'x', 'value': '1'}).status_code, 403)
        detail = reverse('setting-detail', args=[Setting.objects.get().pk])
        self.assertEqual(client.patch(detail, {'value': '99'}).status_code, 403)
        self.assertEqual(client.delete(detail).status_code, 403)
        self.assertEqual(Setting.objects.get().value, '25')

    def test_staff_can_manage_settings(self):
        client = self.as_user(self.staff)
        response = client.post(self.url, {'key': 'default_margin_percent', 'value': '25', 'description': 'Retail margin'})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Setting.objects.get().company, self.company)
        detail = reverse('setting-detail', args=[response.json()['id']])
        self.assertEqual(client.patch(detail, {'value': '30'}).json()['value'], '30')
        self.assertEqual(client.delete(detail).status_code, 204)

    def test_keys_are_unique_per_company_not_globally(self):
        client = self.as_user(self.staff)
        self.assertEqual(client.post(self.url, {'key': 'k', 'value': '1'}).status_code, 201)
        response = client.post(self.url, {'key': 'k', 'value': '2'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('key', response.json())

        other_staff = UserFactory(company=CompanyFactory(), is_staff=True)
        self.assertEqual(self.as_user(other_staff).post(self.url, {'key': 'k', 'value': '3'}).status_code, 201)

    def test_settings_are_private_to_their_company(self):
        Setting.objects.create(company=CompanyFactory(), key='secret', value='hidden')
        self.assertEqual(self.as_user(self.member).get(self.url).json()['results'], [])

    def test_company_cannot_be_chosen_by_the_client(self):
        other = CompanyFactory()
        self.as_user(self.staff).post(self.url, {'key': 'k', 'value': '1', 'company': other.pk})
        self.assertEqual(Setting.objects.get().company, self.company)
