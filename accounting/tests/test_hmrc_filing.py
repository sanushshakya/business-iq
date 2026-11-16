"""G56: obligations, submitting the return to HMRC, checking an unknown result; nothing locks unless HMRC accepted."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.hmrc import client
from accounting.journal import locked_through
from accounting.models import HmrcConnection, HmrcSubmission, Journal, VatReturn
from accounting.tests.test_hmrc_client import SANDBOX, answer
from accounting.tests.test_hmrc_fraud import CLIENT
from accounting.tests.test_vat_return import VatReturnCase
from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK

D = Decimal
RECEIPT = {'processingDate': '2026-11-16T09:30:47Z', 'formBundleNumber': '256660290587',
           'paymentIndicator': 'BANK', 'chargeRefNumber': 'aCxFaNx0FZsCvyWF'}


class FakeHmrc:
    """Answers ``client.SESSION.request`` like HMRC's sandbox; records what was sent."""

    def __init__(self, obligations, submit=None, view=None):
        self.obligations, self.submit, self.view = obligations, submit or answer(201, RECEIPT), view
        self.sent = []

    def __call__(self, method, url, **kwargs):
        self.sent.append((method, url, kwargs))
        if url.endswith('/obligations'):
            return answer(200, {'obligations': self.obligations})
        if method == 'POST' and url.endswith('/returns'):
            if isinstance(self.submit, Exception):
                raise self.submit
            return self.submit
        if '/returns/' in url:
            return self.view
        raise AssertionError(url)

    def posts(self):
        return [kwargs['json'] for method, url, kwargs in self.sent if method == 'POST']


@override_settings(**SANDBOX, HMRC_VENDOR_PUBLIC_IP='203.0.113.6')
class FilingTests(VatReturnCase):
    def setUp(self):
        super().setUp()
        self.company.vat_number = 'GB123456789'
        self.company.save()
        self.connection = HmrcConnection.objects.create(
            company=self.company, vrn='123456789', environment='sandbox', access_token='access-token',
            refresh_token='refresh-token', access_expires_at=timezone.now() + timedelta(hours=4),
            refresh_expires_at=timezone.now() + timedelta(days=500), scope='read:vat write:vat')
        self.end = self.today.replace(day=1) - timedelta(days=1)
        self.start = self.end.replace(day=1)
        self.bill(D('100.00'), D('20.00'), day=self.start + timedelta(days=2))
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.open = [{'start': self.start.isoformat(), 'end': self.end.isoformat(),
                      'due': (self.end + timedelta(days=37)).isoformat(), 'status': 'O', 'periodKey': '24A1'}]

    def submit(self, fake, **body):
        payload = {'period_key': '24A1', 'start': self.start.isoformat(), 'end': self.end.isoformat(),
                   'declaration': True, 'client': CLIENT, **body}
        with mock.patch.object(client.SESSION, 'request', side_effect=fake):
            return self.api.post(reverse('hmrc-returns'), payload, format='json')

    def check(self, submission, fake):
        with mock.patch.object(client.SESSION, 'request', side_effect=fake):
            return self.api.post(reverse('hmrc-return-check', args=[submission.pk]), {'client': CLIENT},
                                 format='json')

    def assert_nothing_filed(self):
        self.assertFalse(VatReturn.objects.exists())
        self.assertFalse(Journal.objects.filter(source='vat_return').exists())
        self.assertIsNone(locked_through(self.company))

    def test_accepted_submission_files_return_and_locks(self):
        response = self.submit(FakeHmrc(self.open))
        self.assertEqual(response.status_code, 201, response.content)
        submission = HmrcSubmission.objects.get()
        self.assertEqual(submission.status, 'accepted')
        self.assertEqual((submission.form_bundle_number, submission.charge_ref_number, submission.payment_indicator),
                         ('256660290587', 'aCxFaNx0FZsCvyWF', 'BANK'))
        filed = VatReturn.objects.get()
        self.assertEqual((filed.period_key, filed.period_start, filed.period_end), ('24A1', self.start, self.end))
        self.assertEqual(filed.hmrc_submission, submission)
        self.assertEqual((filed.box4, filed.box7, filed.net_direction), (D('20.00'), D('100'), 'reclaim'))
        self.assertTrue(Journal.objects.filter(source='vat_return').exists())
        self.assertEqual(locked_through(self.company), self.start)
        self.assertEqual(response.json()['form_bundle_number'], '256660290587')

    def test_body_maps_the_nine_boxes(self):
        fake = FakeHmrc(self.open)
        self.submit(fake)
        self.assertEqual(fake.posts(), [{
            'periodKey': '24A1', 'vatDueSales': 0.0, 'vatDueAcquisitions': 0.0, 'totalVatDue': 0.0,
            'vatReclaimedCurrPeriod': 20.0, 'netVatDue': 20.0, 'totalValueSalesExVAT': 0,
            'totalValuePurchasesExVAT': 100, 'totalValueGoodsSuppliedExVAT': 0, 'totalAcquisitionsExVAT': 0,
            'finalised': True}])
        method, url, kwargs = fake.sent[-1]
        self.assertEqual(url, 'https://test-api.service.hmrc.gov.uk/organisations/vat/123456789/returns')
        self.assertEqual(kwargs['headers']['Gov-Client-Connection-Method'], 'WEB_APP_VIA_SERVER')
        self.assertEqual(kwargs['headers']['Gov-Client-Device-ID'], CLIENT['device_id'])

    def test_rejected_submission_changes_nothing(self):
        refusal = answer(400, {'code': 'INVALID_REQUEST', 'message': 'Invalid request',
                               'errors': [{'code': 'INVALID_MONETARY_AMOUNT', 'message': 'Amount not valid'}]})
        response = self.submit(FakeHmrc(self.open, submit=refusal))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['hmrc_code'], 'INVALID_MONETARY_AMOUNT')
        self.assertEqual(response.json()['message'], 'Amount not valid')
        submission = HmrcSubmission.objects.get()
        self.assertEqual((submission.status, submission.error_code), ('rejected', 'INVALID_MONETARY_AMOUNT'))
        self.assert_nothing_filed()
        # A rejected attempt does not block the next one.
        self.assertEqual(self.submit(FakeHmrc(self.open)).status_code, 201)

    def test_timeout_marks_unknown_and_blocks_a_new_attempt(self):
        response = self.submit(FakeHmrc(self.open, submit=requests.Timeout('slow')))
        self.assertEqual(response.status_code, 202)
        submission = HmrcSubmission.objects.get()
        self.assertEqual(submission.status, 'unknown')
        self.assert_nothing_filed()
        # A new attempt checks HMRC first; HMRC can't say yet (no answer) → still unknown, nothing sent again.
        fake = FakeHmrc(self.open, view=answer(503, {'code': 'SERVICE_UNAVAILABLE', 'message': 'x'}))
        response = self.submit(fake)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(fake.posts(), [])
        self.assertEqual(HmrcSubmission.objects.count(), 1)

    def test_server_error_is_unknown(self):
        self.submit(FakeHmrc(self.open, submit=answer(500, {'code': 'SERVER_ERROR', 'message': 'x'})))
        self.assertEqual(HmrcSubmission.objects.get().status, 'unknown')
        self.assert_nothing_filed()

    def view_of(self, submission, **changes):
        body = {'periodKey': '24A1', 'vatDueSales': float(submission.box1), 'vatDueAcquisitions': 0.0,
                'totalVatDue': float(submission.box3), 'vatReclaimedCurrPeriod': float(submission.box4),
                'netVatDue': float(submission.box5), 'totalValueSalesExVAT': int(submission.box6),
                'totalValuePurchasesExVAT': int(submission.box7), 'totalValueGoodsSuppliedExVAT': 0,
                'totalAcquisitionsExVAT': 0}
        return answer(200, dict(body, **changes))

    def test_check_completes_when_hmrc_has_matching_return(self):
        self.submit(FakeHmrc(self.open, submit=requests.Timeout('slow')))
        submission = HmrcSubmission.objects.get()
        fake = FakeHmrc(self.open, view=self.view_of(submission))
        response = self.check(submission, fake)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['status'], 'accepted')
        self.assertTrue(fake.sent[-1][1].endswith('/organisations/vat/123456789/returns/24A1'))
        self.assertEqual(VatReturn.objects.get().hmrc_submission, submission)
        self.assertEqual(locked_through(self.company), self.start)

    def test_check_rejects_when_hmrc_has_none(self):
        self.submit(FakeHmrc(self.open, submit=requests.Timeout('slow')))
        submission = HmrcSubmission.objects.get()
        response = self.check(submission, FakeHmrc(self.open, view=answer(404, {'code': 'NOT_FOUND',
                                                                                 'message': 'not found'})))
        self.assertEqual(response.json()['status'], 'rejected')
        self.assertEqual(response.json()['error_code'], 'NOT_RECEIVED')
        self.assert_nothing_filed()
        self.assertEqual(self.submit(FakeHmrc(self.open)).status_code, 201)

    def test_check_keeps_unknown_when_figures_differ(self):
        self.submit(FakeHmrc(self.open, submit=requests.Timeout('slow')))
        submission = HmrcSubmission.objects.get()
        response = self.check(submission, FakeHmrc(self.open, view=self.view_of(submission, netVatDue=99.0)))
        self.assertEqual(response.json()['status'], 'unknown')
        self.assertEqual(response.json()['error_code'], 'FIGURES_DIFFER')
        self.assert_nothing_filed()

    def test_duplicate_submission_is_checked_not_resent(self):
        duplicate = answer(403, {'code': 'DUPLICATE_SUBMISSION', 'message': 'already submitted'})
        response = self.submit(FakeHmrc(self.open, submit=duplicate))
        self.assertEqual(response.status_code, 202)
        submission = HmrcSubmission.objects.get()
        self.assertEqual((submission.status, submission.error_code), ('unknown', 'DUPLICATE_SUBMISSION'))
        fake = FakeHmrc(self.open, view=self.view_of(submission))
        self.assertEqual(self.check(submission, fake).json()['status'], 'accepted')
        self.assertEqual(fake.posts(), [])

    def test_second_submission_while_sending_is_refused(self):
        HmrcSubmission.objects.create(company=self.company, period_key='24A1', period_start=self.start,
                                      period_end=self.end, box1=0, box2=0, box3=0, box4=0, box5=0, box6=0, box7=0,
                                      box8=0, box9=0, net_direction='pay', status='sending')
        fake = FakeHmrc(self.open)
        response = self.submit(fake)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(fake.posts(), [])

    def test_vrn_changed_since_connect_is_refused(self):
        self.company.vat_number = 'GB987654321'
        self.company.save()
        fake = FakeHmrc(self.open)
        response = self.submit(fake)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Connect to HMRC again', response.json()['detail'])
        self.assertEqual(fake.sent, [])

    def test_period_must_be_an_open_obligation_and_whole_months(self):
        response = self.submit(FakeHmrc([]))
        self.assertEqual(response.status_code, 400)
        self.assertIn('open HMRC period', response.json()['detail'])
        odd = [dict(self.open[0], start=(self.start + timedelta(days=1)).isoformat())]
        response = self.submit(FakeHmrc(odd), start=(self.start + timedelta(days=1)).isoformat())
        self.assertEqual(response.status_code, 400)
        self.assertIn('whole months', response.json()['detail'])
        self.assertFalse(HmrcSubmission.objects.exists())

    def test_declaration_required(self):
        response = self.submit(FakeHmrc(self.open), declaration=False)
        self.assertEqual(response.status_code, 400)
        self.assertIn('declaration', response.json())

    def test_client_info_required(self):
        response = self.submit(FakeHmrc(self.open), client={})
        self.assertEqual(response.status_code, 400)
        self.assertIn('client', response.json())

    def test_not_connected(self):
        self.connection.delete()
        response = self.submit(FakeHmrc(self.open))
        self.assertEqual(response.status_code, 400)
        self.assertIn('Connect to HMRC', response.json()['detail'])

    def test_mark_as_filed_refused_when_hmrc_on(self):
        response = self.api.post(reverse('vatreturn-list'), {'from': self.start.isoformat(),
                                                             'to': self.end.isoformat()})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Submit to HMRC', str(response.json()))
        with override_settings(HMRC_VAT_ENVIRONMENT='off', HMRC_BASE_URL=''):
            response = self.api.post(reverse('vatreturn-list'), {'from': self.start.isoformat(),
                                                                 'to': self.end.isoformat()})
            self.assertEqual(response.status_code, 201)

    def test_obligations_mark_filed_in_iq(self):
        self.submit(FakeHmrc(self.open))
        fake = FakeHmrc(self.open + [dict(self.open[0], periodKey='24A2', start='2026-01-01', end='2026-03-31')])
        with mock.patch.object(client.SESSION, 'request', side_effect=fake):
            response = self.api.post(reverse('hmrc-obligations'), {'client': CLIENT, 'status': 'O'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        rows = {row['periodKey']: row for row in response.json()}
        self.assertTrue(rows['24A1']['filed_in_iq'])
        self.assertFalse(rows['24A2']['filed_in_iq'])
        params = fake.sent[-1][2]['params']
        self.assertEqual(params['status'], 'O')
        self.assertEqual(params['to'], self.today.isoformat())

    def test_submissions_list_and_permissions(self):
        self.submit(FakeHmrc(self.open))
        member = APIClient()
        member.force_authenticate(UserFactory(company=self.company))
        self.assertEqual(len(member.get(reverse('hmrc-returns')).json()), 1)
        with mock.patch.object(client.SESSION, 'request', side_effect=FakeHmrc(self.open)):
            self.assertEqual(member.post(reverse('hmrc-obligations'), {'client': CLIENT}, format='json').status_code,
                             200)
            payload = {'period_key': '24A1', 'start': self.start.isoformat(), 'end': self.end.isoformat(),
                       'declaration': True, 'client': CLIENT}
            self.assertEqual(member.post(reverse('hmrc-returns'), payload, format='json').status_code, 403)
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        picker = APIClient()
        picker.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(picker.get(reverse('hmrc-returns')).status_code, 403)
        stranger = APIClient()
        stranger.force_authenticate(UserFactory(company=CompanyFactory(), is_staff=True))
        self.assertEqual(stranger.get(reverse('hmrc-returns')).json(), [])
        response = stranger.post(reverse('hmrc-return-check', args=[HmrcSubmission.objects.get().pk]),
                                 {'client': CLIENT}, format='json')
        self.assertEqual(response.status_code, 404)
