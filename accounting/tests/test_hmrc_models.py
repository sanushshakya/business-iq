"""G56: HMRC settings and the connection, sign-in state and submission models."""

import datetime

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from accounting.models import HmrcConnection, HmrcSubmission
from common.tests.factories import CompanyFactory
from common.tests.test_settings import STRONG_KEY, load_settings


class HmrcSettingsTests(SimpleTestCase):
    def test_environment_must_be_known(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'HMRC_VAT_ENVIRONMENT'):
            load_settings(SECRET_KEY=STRONG_KEY, HMRC_VAT_ENVIRONMENT='live')
        self.assertEqual(load_settings(SECRET_KEY=STRONG_KEY, HMRC_VAT_ENVIRONMENT='sandbox')['HMRC_BASE_URL'],
                         'https://test-api.service.hmrc.gov.uk')
        self.assertEqual(load_settings(SECRET_KEY=STRONG_KEY, HMRC_VAT_ENVIRONMENT='production')['HMRC_BASE_URL'],
                         'https://api.service.hmrc.gov.uk')
        self.assertEqual(load_settings(SECRET_KEY=STRONG_KEY, HMRC_VAT_ENVIRONMENT='off')['HMRC_BASE_URL'], '')


def submission(company, status, key='24A1'):
    return HmrcSubmission.objects.create(
        company=company, period_key=key, period_start=datetime.date(2026, 7, 1),
        period_end=datetime.date(2026, 9, 30), box1=0, box2=0, box3=0, box4=0, box5=0, box6=0, box7=0, box8=0,
        box9=0, net_direction='pay', status=status)


class HmrcModelTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory(name='Test Foods Ltd')

    def test_tokens_are_encrypted_at_rest(self):
        HmrcConnection.objects.create(
            company=self.company, vrn='123456789', environment='sandbox', access_token='plain-access-token-xyz',
            refresh_token='plain-refresh-token-xyz', access_expires_at=timezone.now(),
            refresh_expires_at=timezone.now(), scope='read:vat write:vat')
        with connection.cursor() as cursor:
            cursor.execute('SELECT access_token, refresh_token FROM accounting_hmrcconnection')
            access, refresh = cursor.fetchone()
        self.assertNotIn('plain-access-token-xyz', access)
        self.assertNotIn('plain-refresh-token-xyz', refresh)
        self.assertEqual(HmrcConnection.objects.get().access_token, 'plain-access-token-xyz')

    def test_only_one_open_submission_per_period(self):
        submission(self.company, HmrcSubmission.SENDING)
        submission(self.company, HmrcSubmission.REJECTED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            submission(self.company, HmrcSubmission.UNKNOWN)
        submission(self.company, HmrcSubmission.SENDING, key='24A2')
