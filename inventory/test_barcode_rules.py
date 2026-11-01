# inventory/test_barcode_rules.py

"""G15: per-company barcode rules — tenancy, layout validation and what the till reads."""

import json
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from inventory.barcode_parser import BarcodeError, parse
from inventory.models import BarcodeRule

FIXTURES = json.loads((Path(__file__).parent / 'barcode_fixtures.json').read_text())
PRICE = {'name': 'Fake price labels', 'prefix': '20', 'item_code_length': 5, 'value_type': 'price', 'decimals': 2,
         'value_check_digit': False}


class BarcodeRuleApiTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.staff = APIClient()
        self.staff.force_authenticate(UserFactory(company=self.company, is_staff=True))
        self.list_url = reverse('barcoderule-list')

    def rule(self, company=None, **fields):
        return BarcodeRule.objects.create(company=company or self.company, **{**PRICE, **fields})

    def operator(self, role):
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Operator', role)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        return api

    def test_staff_create_and_list(self):
        response = self.staff.post(self.list_url, PRICE, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body['prefix'], body['value_length'], body['is_active']), ('20', 5, True))
        self.assertEqual(len(self.staff.get(self.list_url).json()['results']), 1)

    def test_other_companys_rule_is_404_and_not_listed(self):
        theirs = self.rule(company=CompanyFactory())
        detail = reverse('barcoderule-detail', args=[theirs.pk])
        self.assertEqual(self.staff.get(detail).status_code, 404)
        self.assertEqual(self.staff.patch(detail, {'name': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.staff.delete(detail).status_code, 404)
        self.assertEqual(self.staff.get(self.list_url).json()['results'], [])

    def test_sent_company_is_ignored(self):
        other = CompanyFactory()
        response = self.staff.post(self.list_url, {**PRICE, 'company': other.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(BarcodeRule.objects.get().company, self.company)

    def test_same_prefix_twice_is_400_but_fine_across_companies(self):
        self.rule(company=CompanyFactory())
        self.assertEqual(self.staff.post(self.list_url, PRICE, format='json').status_code, 201)
        response = self.staff.post(self.list_url, PRICE, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('prefix', response.json())

    def test_rule_layout_validated(self):
        for change, field in (({'item_code_length': 7}, 'item_code_length'),   # 3 value digits
                              ({'item_code_length': 3}, 'item_code_length'),   # 7 value digits
                              ({'prefix': '2', 'item_code_length': 4, 'value_check_digit': True}, 'value_check_digit'),
                              ({'prefix': '30'}, 'prefix'), ({'prefix': '2x'}, 'prefix'), ({'decimals': 4}, 'decimals')):
            response = self.staff.post(self.list_url, {**PRICE, **change}, format='json')
            self.assertEqual(response.status_code, 400, change)
            self.assertIn(field, response.json(), change)
        rule = self.rule()
        response = self.staff.patch(reverse('barcoderule-detail', args=[rule.pk]), {'item_code_length': 8},
                                    format='json')
        self.assertEqual(response.status_code, 400)

    def test_active_filter(self):
        self.rule()
        self.rule(prefix='24', is_active=False)
        prefixes = [r['prefix'] for r in self.staff.get(self.list_url, {'active': 'true'}).json()['results']]
        self.assertEqual(prefixes, ['20'])
        self.assertEqual(len(self.staff.get(self.list_url).json()['results']), 2)

    def test_till_reads_but_cannot_write(self):
        rule = self.rule()
        till = self.operator('till')
        self.assertEqual(till.get(self.list_url, {'active': 'true'}).status_code, 200)
        self.assertEqual(till.get(reverse('barcoderule-detail', args=[rule.pk])).status_code, 200)
        self.assertEqual(till.post(self.list_url, PRICE, format='json').status_code, 403)
        self.assertEqual(till.patch(reverse('barcoderule-detail', args=[rule.pk]), {'name': 'x'},
                                    format='json').status_code, 403)

    def test_other_operator_roles_403(self):
        self.rule()
        for role in ('demand', 'stock'):
            self.assertEqual(self.operator(role).get(self.list_url).status_code, 403, role)

    def test_non_staff_member_reads_only(self):
        member = APIClient()
        member.force_authenticate(UserFactory(company=self.company))
        self.assertEqual(member.get(self.list_url).status_code, 200)
        self.assertEqual(member.post(self.list_url, PRICE, format='json').status_code, 403)

    def test_inactive_rule_ignored(self):
        code = next(case['code'] for case in FIXTURES['cases'] if case['name'] == 'price')
        rule = self.rule()
        self.assertEqual(parse(code, [rule.to_rule()]).price, Decimal('3.45'))
        rule.is_active = False
        with self.assertRaises(BarcodeError) as caught:
            parse(code, [rule.to_rule()])
        self.assertEqual(caught.exception.reason, 'unknown_prefix')


class BarcodeRuleReviewTests(TestCase):
    """G15 review: defaults on create, the admin form, and duplicate prefixes never 500."""

    def setUp(self):
        self.company = CompanyFactory()
        self.staff = APIClient()
        self.staff.force_authenticate(UserFactory(company=self.company, is_staff=True))

    def test_defaults_apply_when_left_out(self):
        response = self.staff.post(reverse('barcoderule-list'),
                                   {'prefix': '20', 'item_code_length': 5, 'value_type': 'price'}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()['decimals'], response.json()['value_check_digit']), (2, False))

    def test_model_clean_with_blank_fields_reports_errors(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError) as caught:
            BarcodeRule(company=self.company, prefix='20', value_type='price').full_clean()
        self.assertIn('item_code_length', caught.exception.message_dict)

    def test_model_clean_refuses_a_duplicate_prefix(self):
        from django.core.exceptions import ValidationError

        BarcodeRule.objects.create(company=self.company, **PRICE)
        with self.assertRaises(ValidationError) as caught:
            BarcodeRule(company=self.company, **PRICE).full_clean(exclude=['company'])  # as the admin form does
        self.assertIn('prefix', caught.exception.message_dict)
        BarcodeRule(company=CompanyFactory(), **PRICE).full_clean()  # another company may use it

    def test_prefix_validator_on_the_model(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError) as caught:
            BarcodeRule(company=self.company, **{**PRICE, 'prefix': '2a'}).full_clean()
        self.assertIn('prefix', caught.exception.message_dict)

    def test_lost_race_on_a_prefix_is_400(self):
        from unittest import mock

        from django.db import IntegrityError

        with mock.patch('inventory.serializers.BarcodeRuleSerializer.create',
                        side_effect=IntegrityError('unique_barcode_prefix_per_company')):
            response = self.staff.post(reverse('barcoderule-list'), PRICE, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('prefix', response.json())
