# vat/tests/test_vat.py

from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from tenants.models import Company

User = get_user_model()


class SplitInclusiveTests(TestCase):
    def test_split_inclusive_standard(self):
        from vat.services import split_inclusive

        self.assertEqual(split_inclusive(Decimal('1.20'), Decimal('20.00')), (Decimal('1.00'), Decimal('0.20')))

    def test_half_penny_rounds_up(self):
        from vat.services import split_inclusive

        # 0.21 x 20/120 = 0.035 -> 0.04
        self.assertEqual(split_inclusive(Decimal('0.21'), Decimal('20.00')), (Decimal('0.17'), Decimal('0.04')))

    def test_reduced_rate(self):
        from vat.services import split_inclusive

        # 2.10 x 5/105 = 0.10
        self.assertEqual(split_inclusive(Decimal('2.10'), Decimal('5.00')), (Decimal('2.00'), Decimal('0.10')))

    def test_zero_rate_has_no_vat(self):
        from vat.services import split_inclusive

        self.assertEqual(split_inclusive(Decimal('3.33'), Decimal('0.00')), (Decimal('3.33'), Decimal('0.00')))

    def test_results_are_decimals_with_two_places(self):
        from vat.services import split_inclusive

        net, vat = split_inclusive(Decimal('3.75'), Decimal('20.00'))
        self.assertEqual((str(net), str(vat)), ('3.12', '0.63'))


class RateTableTests(TestCase):
    def test_seeded_uk_rates(self):
        from vat.services import rate_for

        today = date(2026, 10, 29)
        self.assertEqual(rate_for('zero', today), Decimal('0.00'))
        self.assertEqual(rate_for('exempt', today), Decimal('0.00'))
        self.assertEqual(rate_for('reduced', today), Decimal('5.00'))
        self.assertEqual(rate_for('standard', today), Decimal('20.00'))

    def test_rate_changes_on_valid_from_day(self):
        from vat.models import VatRate
        from vat.services import rate_for

        VatRate.objects.create(code='standard', rate=Decimal('22.50'), valid_from=date(2030, 1, 1))
        self.assertEqual(rate_for('standard', date(2029, 12, 31)), Decimal('20.00'))
        self.assertEqual(rate_for('standard', date(2030, 1, 1)), Decimal('22.50'))

    def test_missing_rate_raises(self):
        from vat.services import NoVatRate, rate_for

        with self.assertRaises(NoVatRate):
            rate_for('standard', date(1960, 1, 1))


class RateApiTests(TestCase):
    def setUp(self):
        from authentication import refresh_tokens

        self.company = Company.objects.create(name='Fake Foods Ltd', registration_number='FAKE1')
        self.staff = User.objects.create_user(email='boss@example.com', password='pw-12345678', company=self.company,
                                              is_staff=True)
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(self.staff)['access_token']}")

    def test_rates_api_lists_and_is_read_only(self):
        response = self.api.get(reverse('vat-rate-list'))
        self.assertEqual(response.status_code, 200)
        rows = {(r['code'], r['rate'], r['valid_from']) for r in response.json()['results']}
        self.assertIn(('standard', '20.00', '2011-01-04'), rows)
        post = self.api.post(reverse('vat-rate-list'), {'code': 'standard', 'rate': '1.00', 'valid_from': '2030-01-01'})
        self.assertEqual(post.status_code, 405)

    def test_operator_gets_403(self):
        from authentication import refresh_tokens

        operator, _code = User.objects.create_operator(self.company, 'Fake Till', 'till')
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(api.get(reverse('vat-rate-list')).status_code, 403)


class RateAdminTests(TestCase):
    def test_only_superuser_can_change_rates_in_admin(self):
        from vat.models import VatRate

        model_admin = admin.site._registry[VatRate]
        company = Company.objects.create(name='Fake Foods Ltd', registration_number='FAKE1')
        request = RequestFactory().get('/admin/')
        request.user = User.objects.create_user(email='boss@example.com', password='pw-12345678', company=company,
                                                is_staff=True)
        self.assertTrue(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        request.user = User.objects.create_superuser(email='root@example.com', password='pw-12345678')
        self.assertTrue(model_admin.has_add_permission(request))
        self.assertTrue(model_admin.has_change_permission(request))

    def test_rates_in_force_cannot_be_edited_or_deleted(self):
        from vat.models import VatRate

        model_admin = admin.site._registry[VatRate]
        request = RequestFactory().get('/admin/')
        request.user = User.objects.create_superuser(email='root@example.com', password='pw-12345678')
        in_force = VatRate.objects.get(code='standard', valid_from=date(2011, 1, 4))
        future = VatRate.objects.create(code='standard', rate=Decimal('22.50'), valid_from=date(2099, 1, 1))
        self.assertEqual(set(model_admin.get_readonly_fields(request, in_force)), {'code', 'rate', 'valid_from'})
        self.assertFalse(model_admin.has_delete_permission(request, in_force))
        self.assertEqual(list(model_admin.get_readonly_fields(request, future)), [])
        self.assertTrue(model_admin.has_delete_permission(request, future))


class SeedMigrationTests(TestCase):
    """
    The seed migration's forward and reverse functions, run against the real registry inside a transaction.

    (Not a TransactionTestCase: its flush would delete the seeded rates every other test relies on.)
    """

    def setUp(self):
        import importlib

        from django.apps import apps

        self.migration = importlib.import_module('vat.migrations.0002_seed_uk_rates')
        self.apps = apps

    def test_seed_migration_reverses_and_keeps_rows_added_by_hand(self):
        from vat.models import VatRate

        self.assertEqual(VatRate.objects.count(), 4)
        VatRate.objects.create(code='standard', rate=Decimal('22.00'), valid_from=date(2030, 1, 1))
        self.migration.unseed(self.apps, None)
        self.assertEqual(list(VatRate.objects.values_list('rate', flat=True)), [Decimal('22.00')])
        self.migration.seed(self.apps, None)
        self.assertEqual(VatRate.objects.count(), 5)

    def test_seed_is_idempotent(self):
        from vat.models import VatRate

        self.migration.seed(self.apps, None)
        self.assertEqual(VatRate.objects.count(), 4)


class SchemaTests(TestCase):
    def test_schema_generates_without_warnings(self):
        from io import StringIO

        from django.core.management import call_command

        call_command('spectacular', '--file', '/dev/null', '--fail-on-warn', stderr=StringIO())
