"""G56: ``manage.py hmrc_sandbox_check`` (the Done-when run against HMRC's sandbox), with HMRC mocked."""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from accounting.hmrc import client
from accounting.models import HmrcConnection, HmrcSubmission
from accounting.tests.test_hmrc_client import SANDBOX, answer
from accounting.tests.test_hmrc_filing import FakeHmrc
from accounting.tests.test_vat_return import VatReturnCase

D = __import__('decimal').Decimal


@override_settings(**SANDBOX)
class SandboxCommandTests(VatReturnCase):
    def setUp(self):
        super().setUp()
        self.company.vat_number = 'GB123456789'
        self.company.save()
        HmrcConnection.objects.create(
            company=self.company, vrn='123456789', environment='sandbox', access_token='access-token-abc',
            refresh_token='refresh-token-abc', access_expires_at=timezone.now() + timedelta(hours=4),
            refresh_expires_at=timezone.now() + timedelta(days=500), scope='read:vat write:vat')
        self.end = self.today.replace(day=1) - timedelta(days=1)
        self.start = self.end.replace(day=1)
        self.bill(D('100.00'), D('20.00'), day=self.start + timedelta(days=2))
        self.open = [{'start': self.start.isoformat(), 'end': self.end.isoformat(),
                      'due': (self.end + timedelta(days=37)).isoformat(), 'status': 'O', 'periodKey': '24A1'}]

    def run_command(self, fake, *args):
        verdict = answer(200, {'code': 'VALID_HEADERS', 'message': 'All headers appear to be valid'})

        def route(method, url, **kwargs):
            return verdict if url.endswith('/validate') else fake(method, url, **kwargs)

        out = StringIO()
        with mock.patch.object(client.SESSION, 'request', side_effect=route):
            call_command('hmrc_sandbox_check', '--company', str(self.company.pk), '--user', str(self.staff.pk),
                         *args, stdout=out)
        return out.getvalue()

    def test_checks_headers_lists_obligations_and_submits(self):
        out = self.run_command(FakeHmrc(self.open), '--submit', '24A1')
        self.assertIn('VALID_HEADERS', out)
        self.assertIn('24A1', out)
        self.assertIn('accepted', out)
        self.assertIn('256660290587', out)
        self.assertNotIn('access-token-abc', out)
        self.assertEqual(HmrcSubmission.objects.get().status, 'accepted')

    def test_without_submit_nothing_is_sent(self):
        fake = FakeHmrc(self.open)
        self.run_command(fake)
        self.assertEqual(fake.posts(), [])

    def test_refused_outside_sandbox(self):
        with override_settings(HMRC_VAT_ENVIRONMENT='production', HMRC_BASE_URL='https://api.service.hmrc.gov.uk'):
            with self.assertRaisesMessage(CommandError, 'runs only against the HMRC sandbox'):
                call_command('hmrc_sandbox_check', '--company', str(self.company.pk), '--user', str(self.staff.pk))
