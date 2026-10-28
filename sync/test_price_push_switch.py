from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import Permission
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from common.tenancy import scope_queryset
from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from sync.models import PricePushChange, StockReconciliation, StorefrontConnection

BASE = 'https://m18.example.com/'


class PricePushAuditTests(TestCase):
    """Every switch of ``push_prices`` on or off leaves a row saying who did it and when."""

    def setUp(self):
        self.company = CompanyFactory()
        self.connection = StorefrontConnection.objects.create(company=self.company, base_url=BASE, webhook_secret='s')

    def test_toggling_the_flag_writes_an_audit_row(self):
        self.assertFalse(PricePushChange.objects.exists())  # created off: nothing to record
        self.connection.push_prices = True
        self.connection.save()
        self.connection.name = 'Renamed'
        self.connection.save()  # no change to the flag
        self.connection.push_prices = False
        self.connection.save()
        self.assertEqual(list(PricePushChange.objects.order_by('pk').values_list('turned_on', 'changed_by')),
                         [(True, None), (False, None)])

    def test_created_switched_on_is_recorded(self):
        connection = StorefrontConnection.objects.create(company=CompanyFactory(), webhook_secret='s', push_prices=True)
        self.assertEqual(list(connection.price_push_changes.values_list('turned_on', flat=True)), [True])

    def test_the_admin_records_who_switched_it(self):
        staff = UserFactory(company=self.company, is_staff=True)
        staff.user_permissions.set(Permission.objects.filter(content_type__app_label='sync'))
        self.client.force_login(staff)
        url = reverse('admin:sync_storefrontconnection_change', args=[self.connection.pk])
        response = self.client.post(url, {'company': self.company.pk, 'name': 'M18 storefront', 'base_url': BASE, 'is_active': 'on',
                                          'push_prices': 'on'})
        self.assertEqual(response.status_code, 302, response.context and response.context['adminform'].form.errors)
        change = PricePushChange.objects.get()
        self.assertEqual((change.connection, change.turned_on, change.changed_by), (self.connection, True, staff))
        self.assertIsNotNone(change.changed_at)
        self.assertEqual(self.client.get(reverse('admin:sync_pricepushchange_add')).status_code, 403)

    def test_audit_rows_stay_within_the_company(self):
        self.connection.push_prices = True
        self.connection.save()
        outsider = UserFactory(company=CompanyFactory())
        self.assertFalse(scope_queryset(PricePushChange.objects.all(), outsider).exists())
        self.assertTrue(scope_queryset(PricePushChange.objects.all(), UserFactory(company=self.company)).exists())


class PricePushReadinessTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory(registration_number='READY-1')
        self.connection = StorefrontConnection.objects.create(company=self.company, base_url=BASE, webhook_secret='s')

    def run_command(self, *args):
        out = StringIO()
        call_command('price_push_readiness', '--company', 'READY-1', *args, stdout=out)
        return out.getvalue()

    def report(self, mismatches, hours_ago=1, error=''):
        run = StockReconciliation.objects.create(connection=self.connection, mismatches=mismatches, error=error)
        StockReconciliation.objects.filter(pk=run.pk).update(ran_at=timezone.now() - timedelta(hours=hours_ago))
        return run

    def gap(self, product_id, iq, m18):
        return {'product_id': product_id, 'kind': 'price_different', 'iq': iq, 'm18': m18}

    def test_counts_and_largest_gaps_first(self):
        self.report([self.gap('a', '2.00', '2.10'), self.gap('b', '3.00', '9.00'),
                     {'product_id': 'c', 'kind': 'different', 'iq': 1, 'm18': 2}, self.gap('c', '5.00', '4.00')])
        out = self.run_command('--top', '2')
        self.assertIn('Price differences: 3', out)
        self.assertLess(out.index('b: IQ 3.00, m18 9.00 (+6.00)'), out.index('c: IQ 5.00, m18 4.00 (-1.00)'))
        self.assertNotIn('a: IQ', out)
        self.assertIn('Ready: yes', out)

    def test_out_of_range_prices_block(self):
        self.report([])
        ProductFactory(company=self.company, external_id='z', name='Free sample', price=Decimal('0.00'))
        ProductFactory(company=self.company, external_id='big', name='Whole lamb', price=Decimal('1200.00'))
        ProductFactory(company=self.company, external_id='', name='Not linked', price=Decimal('0.00'))
        ProductFactory(company=CompanyFactory(), external_id='x', name='Their product', price=Decimal('0.00'))
        out = self.run_command()
        self.assertIn('Out-of-range prices (m18 refuses them): 2', out)
        self.assertIn('Free sample (z): 0.00', out)
        self.assertIn('Whole lamb (big): 1200.00', out)
        self.assertNotIn('Not linked', out)
        self.assertNotIn('Their product', out)
        self.assertIn('Ready: no', out)

    def test_a_missing_stale_or_failed_report_blocks(self):
        self.assertIn('Ready: no — no nightly report yet', self.run_command())
        self.report([], hours_ago=50)
        self.assertIn('older than 48 h', self.run_command())
        self.report([], error='HTTP 502')
        self.assertIn('the last report failed: HTTP 502', self.run_command())

    def test_shows_the_switch_and_its_history(self):
        self.report([])
        self.connection.push_prices = True
        self.connection.save()
        out = self.run_command()
        self.assertIn('push_prices: on', out)
        self.assertIn('switched on', out)

    def test_unknown_company_or_no_connection_is_an_error(self):
        with self.assertRaisesMessage(CommandError, 'No company'):
            call_command('price_push_readiness', '--company', 'NOPE', stdout=StringIO())
        CompanyFactory(registration_number='BARE')
        with self.assertRaisesMessage(CommandError, 'no storefront connection'):
            call_command('price_push_readiness', '--company', 'BARE', stdout=StringIO())
