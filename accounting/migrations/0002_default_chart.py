"""Existing companies get the default chart of accounts (G52); new ones get it from a signal. Back: removes only
default-chart accounts nothing has been posted to."""

from django.db import migrations

# Frozen here, so a later change to accounting.chart does not change what this migration did.
DEFAULT_CHART = [
    ('1000', 'Bank current account', 'asset', 'bank'),
    ('1010', 'Cash in tills', 'asset', 'cash'),
    ('1020', 'Card clearing', 'asset', 'card_clearing'),
    ('1100', 'Trade debtors', 'asset', 'debtors'),
    ('1200', 'Stock', 'asset', 'stock'),
    ('2100', 'Trade creditors', 'liability', 'creditors'),
    ('2200', 'VAT on sales', 'liability', 'vat_output'),
    ('2210', 'VAT on purchases', 'liability', 'vat_input'),
    ('2220', 'VAT due to HMRC', 'liability', 'vat_hmrc'),
    ('3000', 'Capital', 'equity', 'capital'),
    ('3100', 'Retained earnings', 'equity', 'retained_earnings'),
    ('4000', 'Sales - standard rate', 'income', 'sales_standard'),
    ('4010', 'Sales - reduced rate', 'income', 'sales_reduced'),
    ('4020', 'Sales - zero rate', 'income', 'sales_zero'),
    ('4030', 'Sales - exempt', 'income', 'sales_exempt'),
    ('4900', 'Other income', 'income', None),
    ('5000', 'Purchases', 'expense', 'purchases'),
    ('5100', 'Stock wastage', 'expense', 'wastage'),
    ('5110', 'Butchery yield loss', 'expense', 'yield_loss'),
    ('5200', 'Carriage inwards', 'expense', None),
    ('7000', 'Wages', 'expense', None),
    ('7100', 'Rent and rates', 'expense', None),
    ('7200', 'Utilities', 'expense', None),
    ('7500', 'Bank charges', 'expense', None),
    ('7510', 'Card fees', 'expense', None),
    ('9999', 'Suspense', 'asset', 'suspense'),
]
DEFAULT_CODES = [code for code, *_rest in DEFAULT_CHART]


def add_default_chart(apps, schema_editor):
    Company = apps.get_model('tenants', 'Company')
    Account = apps.get_model('accounting', 'Account')
    for company in Company.objects.all():
        have = set(Account.objects.filter(company=company).values_list('code', flat=True))
        keys = set(Account.objects.filter(company=company).exclude(system_key=None)
                   .values_list('system_key', flat=True))
        Account.objects.bulk_create([
            Account(company=company, code=code, name=name, type=kind, system_key=key if key not in keys else None)
            for code, name, kind, key in DEFAULT_CHART if code not in have])


def remove_default_chart(apps, schema_editor):
    Account = apps.get_model('accounting', 'Account')
    Account.objects.filter(code__in=DEFAULT_CODES, lines__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('accounting', '0001_initial')]

    operations = [migrations.RunPython(add_default_chart, remove_default_chart)]
