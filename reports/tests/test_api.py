"""G59: the dashboard over the API — staff only, own company, a week picked by any day in it."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from reports.tests import test_sales_waste
from reports.tests.helpers import MONDAY
from tenants.roles import STOCK

SUNDAY = MONDAY + timedelta(days=6)


@mock.patch('reports.views.uk_date', lambda: SUNDAY)
class DashboardApiTests(test_sales_waste.SeededWeek):
    url = reverse('reports-dashboard')

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_staff_see_the_week_and_the_one_before(self):
        answer = self.client_for(self.staff).get(self.url, {'week': (MONDAY + timedelta(days=3)).isoformat()})
        self.assertEqual(answer.status_code, 200, answer.content)
        body = answer.json()
        self.assertEqual((body['week_start'], body['previous_start'], body['days']),
                         (MONDAY.isoformat(), (MONDAY - timedelta(days=7)).isoformat(), 7))
        north = next(r for r in body['current']['sales'] if r['branch_name'] == 'Fake North')
        self.assertEqual((north['channel'], north['net'], north['cost'], north['margin_percent'], north['kg']),
                         ('till', '10.50', '7.50', '28.57', '0.500'))
        self.assertEqual((body['current']['totals']['net'], body['previous']['totals']['net']), ('14.50', '2.00'))
        self.assertEqual(body['current']['waste'][0]['cost'], '3.00')
        self.assertTrue(body['current']['stock'])

    def test_default_is_this_week_and_dates_are_checked(self):
        staff = self.client_for(self.staff)
        self.assertEqual(staff.get(self.url).json()['week_start'], MONDAY.isoformat())
        self.assertEqual(staff.get(self.url, {'week': '2026-02-31'}).status_code, 400)
        self.assertEqual(staff.get(self.url, {'week': '1999-12-31'}).status_code, 400)

    def test_members_and_operators_are_refused(self):
        self.assertEqual(self.client_for(UserFactory(company=self.company)).get(self.url).status_code, 403)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Stock', STOCK)
        token = APIClient()
        token.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(token.get(self.url).status_code, 403)

    def test_other_companies_see_their_own_only(self):
        outsider = self.client_for(UserFactory(company=CompanyFactory(), is_staff=True))
        body = outsider.get(self.url).json()
        self.assertEqual((body['current']['sales'], body['current']['stock']), ([], []))
        boss = self.client_for(UserFactory(company=CompanyFactory(), is_staff=True, is_superuser=True))
        self.assertEqual(boss.get(self.url).json()['current']['sales'], [])
