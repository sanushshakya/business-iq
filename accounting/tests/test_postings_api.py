"""G53 over the API: running postings for a range, the trial balance; roles and tenancy."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from accounting.models import Journal
from accounting.tests.test_postings import PostingCase
from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK

D = Decimal


class PostingApiTests(PostingCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.yesterday = self.today - timedelta(days=1)
        self.sale('cash', [('zero', D('2.00'), D('0.00'))], day=self.yesterday)
        self.url = reverse('posting-run')

    def test_run_a_range_then_read_the_trial_balance(self):
        body = {'from': self.yesterday.isoformat(), 'to': self.yesterday.isoformat()}
        self.assertEqual(self.member.post(self.url, body, format='json').status_code, 403)
        done = self.api.post(self.url, body, format='json')
        self.assertEqual(done.status_code, 200)
        self.assertEqual((done.json()['posted'], done.json()['already']), (1, 0))
        self.assertEqual(self.api.post(self.url, body, format='json').json()['already'], 1)
        balance = self.member.get(reverse('trial-balance'), {'on': self.yesterday.isoformat()}).json()
        self.assertEqual((balance['debit'], balance['credit']), ('2.00', '2.00'))
        self.assertEqual([(row['code'], row['debit'], row['credit']) for row in balance['rows']],
                         [('1010', '2.00', '0.00'), ('4020', '0.00', '2.00')])
        self.assertEqual(self.member.get(reverse('trial-balance')).status_code, 200)

    def test_range_rules(self):
        for body in ({'from': self.today.isoformat(), 'to': self.today.isoformat()},  # not over yet
                     {'from': self.yesterday.isoformat(), 'to': (self.yesterday - timedelta(days=1)).isoformat()},
                     {'from': (self.yesterday - timedelta(days=366)).isoformat(), 'to': self.yesterday.isoformat()},
                     {'from': '2026-1-1', 'to': self.yesterday.isoformat()}):
            with self.subTest(body=body):
                self.assertEqual(self.api.post(self.url, body, format='json').status_code, 400)
        self.assertEqual(self.member.get(reverse('trial-balance'), {'on': 'soon'}).status_code, 400)

    def test_other_companies_and_operators(self):
        stranger = APIClient()
        stranger.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True, is_superuser=True))
        body = {'from': self.yesterday.isoformat(), 'to': self.yesterday.isoformat()}
        self.assertEqual(stranger.post(self.url, body, format='json').json()['posted'], 0)
        self.assertFalse(Journal.objects.filter(company=self.company).exists())
        self.assertEqual(stranger.get(reverse('trial-balance')).json()['rows'], [])
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        picker = APIClient()
        picker.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(picker.get(reverse('trial-balance')).status_code, 403)
        self.assertEqual(picker.post(self.url, body, format='json').status_code, 403)
