"""G52: the default UK chart — every new company gets it once; the migration adds it to existing ones and back."""

import importlib

from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounting.chart import DEFAULT_CHART, SYSTEM_KEYS, ensure_default_chart
from accounting.models import Account
from common.tests.factories import CompanyFactory


class ChartTests(TestCase):
    def test_a_new_company_has_the_default_chart(self):
        company = CompanyFactory()
        accounts = Account.objects.filter(company=company)
        self.assertEqual(accounts.count(), len(DEFAULT_CHART))
        self.assertEqual(set(accounts.exclude(system_key=None).values_list('system_key', flat=True)), SYSTEM_KEYS)
        for key in ('bank', 'cash', 'card_clearing', 'debtors', 'creditors', 'vat_output', 'vat_input', 'vat_hmrc',
                    'sales_standard', 'sales_reduced', 'sales_zero', 'sales_exempt', 'purchases', 'wastage',
                    'yield_loss', 'stock', 'retained_earnings', 'suspense'):
            self.assertIn(key, SYSTEM_KEYS)
        sales = accounts.get(system_key='sales_standard')
        self.assertEqual((sales.code, sales.type, sales.is_active), ('4000', 'income', True))

    def test_running_it_again_adds_nothing_and_keeps_renames(self):
        company = CompanyFactory()
        Account.objects.filter(company=company, code='7000').update(name='Fake staff costs')
        Account.objects.filter(company=company, code='7100').delete()
        self.assertEqual(ensure_default_chart(company), 1)
        self.assertEqual(Account.objects.filter(company=company).count(), len(DEFAULT_CHART))
        self.assertEqual(Account.objects.get(company=company, code='7000').name, 'Fake staff costs')

    def test_codes_and_system_keys_are_unique_per_company(self):
        company, other = CompanyFactory(), CompanyFactory()
        self.assertTrue(Account.objects.filter(company=other, code='1000').exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.create(company=company, code='1000', name='Fake', type='asset')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.create(company=company, code='1999', name='Fake', type='asset', system_key='bank')

    def test_the_migration_adds_it_and_takes_back_only_unused_defaults(self):
        migration = importlib.import_module('accounting.migrations.0002_default_chart')
        company = CompanyFactory()
        Account.objects.filter(company=company).delete()
        Account.objects.create(company=company, code='6000', name='Fake own account', type='expense')
        migration.add_default_chart(django_apps, None)
        self.assertEqual(Account.objects.filter(company=company).count(), len(DEFAULT_CHART) + 1)
        migration.add_default_chart(django_apps, None)
        self.assertEqual(Account.objects.filter(company=company).count(), len(DEFAULT_CHART) + 1)
        migration.remove_default_chart(django_apps, None)
        self.assertEqual(list(Account.objects.filter(company=company).values_list('code', flat=True)), ['6000'])
