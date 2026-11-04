# tenants/test_branch_code.py

"""G27: a branch's short code, the start of its tills' offline receipt numbers (e.g. HS-T02-QK-000417)."""

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, UserFactory
from tenants.models import Branch


class BranchCodeTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.api = APIClient()
        self.api.force_authenticate(UserFactory(company=self.company, is_staff=True))

    def create(self, code=None, name='Fake High Street'):
        body = {'name': name, 'address': '1 Fake Street', **({'code': code} if code is not None else {})}
        return self.api.post(reverse('branch-list'), body, format='json')

    def test_code_is_stored_in_capitals(self):
        response = self.create(' hs ')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()['code'], Branch.objects.get().code), ('HS', 'HS'))

    def test_code_is_optional_and_blank_many_times(self):
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create(name='Fake Market Street').status_code, 201)
        self.assertEqual(list(Branch.objects.values_list('code', flat=True)), ['', ''])

    def test_bad_codes_refused(self):
        for code in ('HIGHS', 'H', 'H-S', 'H S'):
            with self.subTest(code=code):
                response = self.create(code)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('code', response.json())

    def test_unique_within_a_company_only(self):
        self.assertEqual(self.create('HS').status_code, 201)
        response = self.create('hs', name='Fake Other')
        self.assertEqual(response.status_code, 400)
        self.assertIn('code', response.json())
        Branch.objects.create(company=CompanyFactory(), name='Theirs', address='x', code='HS')  # another company: fine
