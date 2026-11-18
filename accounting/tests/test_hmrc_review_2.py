"""G56 second review: an earlier period still waiting blocks a submit before anything is sent, reading the list
settles stale attempts, a NaN zoom is refused, and recording HMRC's other figures leaves nothing on the VAT accounts."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from accounting.hmrc import fraud
from accounting.journal import account_balance
from accounting.models import HmrcSubmission
from accounting.tests.test_hmrc_client import SANDBOX
from accounting.tests.test_hmrc_filing import FakeHmrc, FilingCase
from accounting.tests.test_hmrc_fraud import CLIENT
from accounting.tests import test_hmrc_review_fixes as fixes

D = Decimal


class NotANumberTests(TestCase):
    def test_a_nan_scaling_factor_is_refused(self):
        for value in ('nan', 'NaN', float('nan')):
            screens = [{'width': 1, 'height': 1, 'scaling_factor': value, 'colour_depth': 24}]
            self.assertFalse(fraud.ClientInfoSerializer(data=dict(CLIENT, screens=screens)).is_valid(), value)


@override_settings(**SANDBOX, HMRC_VENDOR_PUBLIC_IP='203.0.113.6')
class SecondReviewTests(FilingCase):
    unknown = fixes.WayOutTests.unknown
    resolve = fixes.WayOutTests.resolve

    def test_submit_refuses_while_an_earlier_period_waits_for_hmrc(self):
        earlier_end = self.start - timedelta(days=1)
        HmrcSubmission.objects.create(
            company=self.company, period_key='23A4', period_start=earlier_end.replace(day=1), period_end=earlier_end,
            vrn='123456789', box1=0, box2=0, box3=0, box4=0, box5=0, box6=0, box7=0, box8=0, box9=0,
            net_direction='pay', status='unknown', submitted_by=self.staff)
        fake = FakeHmrc(self.open)
        response = self.submit(fake)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn('23A4', response.json()['detail'])
        self.assertEqual(fake.posts(), [])
        self.assertEqual(HmrcSubmission.objects.count(), 1)

    def test_a_later_period_waiting_does_not_block_an_earlier_one(self):
        later_start = self.end + timedelta(days=1)
        HmrcSubmission.objects.create(
            company=self.company, period_key='24A2', period_start=later_start, period_end=self.today,
            vrn='123456789', box1=0, box2=0, box3=0, box4=0, box5=0, box6=0, box7=0, box8=0, box9=0,
            net_direction='pay', status='unknown', submitted_by=self.staff)
        self.assertEqual(self.submit(FakeHmrc(self.open)).status_code, 201)

    def test_reading_the_list_turns_a_stale_sending_attempt_into_unknown(self):
        self.unknown(minutes_ago=10, status='sending')
        row = self.api.get(reverse('hmrc-returns')).json()[0]
        self.assertEqual((row['status'], row['error_code']), ('unknown', 'INTERRUPTED'))

    def test_recording_hmrcs_other_figures_clears_the_vat_accounts_into_suspense(self):
        row = self.unknown(error_code='FIGURES_DIFFER')  # the ledger holds £20.00 VAT on purchases
        held = self.view_of(row, vatReclaimedCurrPeriod=25.0, netVatDue=25.0, totalValuePurchasesExVAT=125)
        response = self.resolve(row, FakeHmrc(self.open, view=held), action='record_hmrc', reason='HMRC copy')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(account_balance(self.key('vat_input')), D('0.00'))
        self.assertEqual(account_balance(self.key('vat_hmrc')), D('25.00'))  # what HMRC repays
        self.assertEqual(account_balance(self.key('suspense')), D('-5.00'))
        self.assertIn('Suspense', response.json()['error_message'])
