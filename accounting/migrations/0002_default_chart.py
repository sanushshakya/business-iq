"""Existing companies get the default chart of accounts (G52); new ones get it from a signal. Back: removes only
default-chart accounts nothing has been posted to."""

from django.db import migrations

# Frozen here, so a later change to accounting.chart does not change what this migration did.
DEFAULT_CODES = ['1000', '1010', '1020', '1100', '1200', '2100', '2200', '2210', '2220', '3000', '3100', '4000',
                 '4010', '4020', '4030', '4900', '5000', '5100', '5110', '5200', '7000', '7100', '7200', '7500',
                 '7510', '9999']


def add_default_chart(apps, schema_editor):
    from accounting.chart import DEFAULT_CHART

    Company = apps.get_model('tenants', 'Company')
    Account = apps.get_model('accounting', 'Account')
    for company in Company.objects.all():
        have = set(Account.objects.filter(company=company).values_list('code', flat=True))
        keys = set(Account.objects.filter(company=company).exclude(system_key=None)
                   .values_list('system_key', flat=True))
        Account.objects.bulk_create([
            Account(company=company, code=code, name=name, type=kind, system_key=key if key not in keys else None)
            for code, name, kind, key in DEFAULT_CHART if code in DEFAULT_CODES and code not in have])


def remove_default_chart(apps, schema_editor):
    Account = apps.get_model('accounting', 'Account')
    Account.objects.filter(code__in=DEFAULT_CODES, lines__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('accounting', '0001_initial')]

    operations = [migrations.RunPython(add_default_chart, remove_default_chart)]
