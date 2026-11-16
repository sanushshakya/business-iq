"""G56 review fixes: every attempt has a way out of ``sending``/``unknown``, the ledger is frozen while HMRC decides,
the sign-in is bound to the browser that started it, and the edges the reviews found."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from accounting.hmrc import client, filing, fraud
from accounting.journal import JournalError, lock_period, locked_through
from accounting.models import HmrcAuthState, HmrcConnection, HmrcSubmission, VatReturn
from accounting.tests.test_hmrc_client import SANDBOX, TOKENS, answer
from accounting.tests.test_hmrc_filing import FakeHmrc, FilingCase
from accounting.tests.test_hmrc_fraud import CLIENT
from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from tenants.roles import STOCK

D = Decimal


class ScalingFactorTests(TestCase):
    def test_a_zoomed_browser_is_accepted_and_rounded(self):
        client_info = dict(CLIENT, screens=[{'width': 1745, 'height': 982, 'scaling_factor': 1.100000023841858,
                                             'colour_depth': 24}])
        serializer = fraud.ClientInfoSerializer(data=client_info)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        request = __import__('django.test', fromlist=['RequestFactory']).RequestFactory().post('/')
        headers = fraud.headers(request, serializer.validated_data, UserFactory())
        self.assertEqual(headers['Gov-Client-Screens'], 'width=1745&height=982&scaling-factor=1.1&colour-depth=24')
        serializer = fraud.ClientInfoSerializer(data=dict(client_info, screens=[dict(client_info['screens'][0],
                                                                                     scaling_factor=0.8999999761581421)]))
        self.assertTrue(serializer.is_valid(), serializer.errors)


@override_settings(**SANDBOX, HMRC_VENDOR_PUBLIC_IP='203.0.113.6')
class WayOutTests(FilingCase):

    def unknown(self, minutes_ago=0, status='unknown', **fields):
        row = HmrcSubmission.objects.create(
            company=self.company, period_key='24A1', period_start=self.start, period_end=self.end, vrn='123456789',
            box1=D('0.00'), box2=D('0.00'), box3=D('0.00'), box4=D('20.00'), box5=D('20.00'), box6=D('0'),
            box7=D('100'), box8=D('0'), box9=D('0'), net_direction='reclaim', status=status, submitted_by=self.staff,
            **fields)
        HmrcSubmission.objects.filter(pk=row.pk).update(submitted_at=timezone.now() - timedelta(minutes=minutes_ago))
        row.refresh_from_db()
        return row

    def test_accepted_with_an_empty_body_is_still_filed(self):
        empty = answer(201)
        empty.json.side_effect = ValueError('no JSON')
        response = self.submit(FakeHmrc(self.open, submit=empty))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(HmrcSubmission.objects.get().status, 'accepted')
        self.assertTrue(VatReturn.objects.exists())

    def test_recording_failure_after_hmrc_accepted_is_unknown_and_can_be_checked(self):
        with mock.patch.object(filing, 'record_return', side_effect=JournalError('boom')):
            response = self.submit(FakeHmrc(self.open))
        self.assertEqual(response.status_code, 202, response.content)
        submission = HmrcSubmission.objects.get()
        self.assertEqual((submission.status, submission.error_code), ('unknown', 'NOT_RECORDED'))
        self.assertFalse(VatReturn.objects.exists())
        response = self.check(submission, FakeHmrc(self.open, view=self.view_of(submission)))
        self.assertEqual(response.json()['status'], 'accepted')
        self.assertEqual(locked_through(self.company), self.start)

    def test_a_stale_sending_attempt_is_checked_like_an_unknown_one(self):
        stale = self.unknown(minutes_ago=10, status='sending')
        response = self.check(stale, FakeHmrc(self.open, view=answer(404, {'code': 'NOT_FOUND', 'message': 'x'})))
        self.assertEqual(response.json()['status'], 'rejected')
        self.assertEqual(response.json()['error_code'], 'NOT_RECEIVED')
        self.assertEqual(self.submit(FakeHmrc(self.open)).status_code, 201)

    def test_a_fresh_sending_attempt_is_left_alone(self):
        fresh = self.unknown(status='sending')
        fake = FakeHmrc(self.open)
        response = self.check(fresh, fake)
        self.assertEqual(response.json()['status'], 'sending')
        self.assertEqual(fake.sent, [])

    def test_the_period_is_frozen_while_hmrc_decides(self):
        self.unknown()
        with self.assertRaisesMessage(JournalError, 'being filed with HMRC'):
            self.bill(D('1.00'), D('0.20'), day=self.start + timedelta(days=3))
        with self.assertRaisesMessage(JournalError, 'waiting for HMRC'):
            lock_period(self.company, self.start, self.staff)
        self.bill(D('1.00'), D('0.20'), day=self.today)  # this month is open as usual

    def test_another_companys_pending_return_freezes_nothing_here(self):
        other = CompanyFactory()
        HmrcSubmission.objects.create(
            company=other, period_key='24A1', period_start=self.start, period_end=self.end, vrn='999999999', box1=0,
            box2=0, box3=0, box4=0, box5=0, box6=0, box7=0, box8=0, box9=0, net_direction='pay', status='unknown')
        self.bill(D('1.00'), D('0.20'), day=self.start + timedelta(days=3))

    def test_check_raises_hmrc_errors_other_than_not_found(self):
        row = self.unknown()
        response = self.check(row, FakeHmrc(self.open, view=answer(503, {'code': 'SERVICE_UNAVAILABLE',
                                                                          'message': 'try later'})))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['hmrc_code'], 'SERVICE_UNAVAILABLE')
        row.refresh_from_db()
        self.assertEqual(row.status, 'unknown')

    def test_resubmitting_a_period_hmrc_turned_out_to_hold_says_it_is_filed(self):
        row = self.unknown()
        fake = FakeHmrc(self.open, view=self.view_of(row))
        response = self.submit(fake)
        self.assertEqual(response.status_code, 409)
        self.assertIn('already filed', response.json()['detail'])
        self.assertEqual(fake.posts(), [])

    def test_a_refused_token_refresh_sends_nothing_and_records_nothing(self):
        HmrcConnection.objects.update(access_expires_at=timezone.now() - timedelta(minutes=1))
        with mock.patch.object(client.SESSION, 'post', side_effect=requests.Timeout('slow')):
            response = self.submit(FakeHmrc(self.open))
        self.assertEqual(response.status_code, 502)
        self.assertFalse(HmrcSubmission.objects.exists())

    def test_check_refuses_a_vrn_other_than_the_one_sent_to(self):
        row = self.unknown()
        HmrcSubmission.objects.filter(pk=row.pk).update(vrn='111111111')
        row.refresh_from_db()
        fake = FakeHmrc(self.open, view=answer(404, {'code': 'NOT_FOUND', 'message': 'x'}))
        response = self.check(row, fake)
        self.assertEqual(response.status_code, 400)
        self.assertIn('VAT number', response.json()['detail'])
        self.assertEqual(fake.sent, [])

    def test_submission_stores_the_vrn(self):
        self.submit(FakeHmrc(self.open))
        self.assertEqual(HmrcSubmission.objects.get().vrn, '123456789')

    def resolve(self, row, fake, **body):
        with mock.patch.object(client.SESSION, 'request', side_effect=fake):
            return self.api.post(reverse('hmrc-return-resolve', args=[row.pk]), {'client': CLIENT, **body},
                                 format='json')

    def test_figures_differ_can_be_recorded_from_hmrcs_copy(self):
        row = self.unknown(error_code='FIGURES_DIFFER')
        held = self.view_of(row, vatReclaimedCurrPeriod=25.0, netVatDue=25.0, totalValuePurchasesExVAT=125)
        response = self.resolve(row, FakeHmrc(self.open, view=held), action='record_hmrc',
                                reason='Our earlier attempt reached HMRC with the old figures')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()['status'], response.json()['error_code']),
                         ('accepted', 'RECORDED_FROM_HMRC'))
        filed = VatReturn.objects.get()
        self.assertEqual((filed.box4, filed.box5, filed.box7, filed.net_direction),
                         (D('25.00'), D('25.00'), D('125'), 'reclaim'))
        self.assertEqual(locked_through(self.company), self.start)

    def test_an_unknown_attempt_can_be_closed_with_a_reason(self):
        row = self.unknown()
        response = self.resolve(row, FakeHmrc(self.open), action='close', reason='Abandoned after a phone call')
        self.assertEqual((response.json()['status'], response.json()['error_code']), ('rejected', 'CLOSED'))
        self.assertIn('Abandoned', response.json()['error_message'])
        self.assertFalse(VatReturn.objects.exists())

    def test_resolve_needs_a_reason_staff_and_an_unknown_attempt(self):
        row = self.unknown()
        self.assertEqual(self.resolve(row, FakeHmrc(self.open), action='close', reason='').status_code, 400)
        member = APIClient()
        member.force_authenticate(UserFactory(company=self.company))
        self.assertEqual(member.post(reverse('hmrc-return-resolve', args=[row.pk]),
                                     {'client': CLIENT, 'action': 'close', 'reason': 'x'}, format='json').status_code,
                         403)
        HmrcSubmission.objects.filter(pk=row.pk).update(status='rejected')
        self.assertEqual(self.resolve(row, FakeHmrc(self.open), action='close', reason='x').status_code, 400)

    def test_operators_get_403_on_every_hmrc_route(self):
        row = self.unknown()
        operator, _code = get_user_model().objects.create_operator(self.company, 'Fake Picker', STOCK)
        picker = APIClient()
        picker.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        for name, args in [('hmrc-obligations', []), ('hmrc-fraud-check', []), ('hmrc-return-check', [row.pk]),
                           ('hmrc-return-resolve', [row.pk])]:
            self.assertEqual(picker.post(reverse(name, args=args), {'client': CLIENT}, format='json').status_code,
                             403, name)

    def test_body_never_sends_negative_zero(self):
        row = self.unknown()
        HmrcSubmission.objects.filter(pk=row.pk).update(box1=D('-0.00'), box3=D('-0.00'))
        row.refresh_from_db()
        sent = filing.body(row)
        self.assertEqual(str(sent['vatDueSales']), '0.0')
        self.assertEqual(str(sent['totalVatDue']), '0.0')

    def test_sandbox_command_runs_as_staff_only(self):
        clerk = UserFactory(company=self.company)
        with self.assertRaisesMessage(CommandError, 'staff'):
            call_command('hmrc_sandbox_check', '--company', str(self.company.pk), '--user', str(clerk.pk),
                         stdout=StringIO())


@override_settings(**SANDBOX)
class BrowserBindingTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory(name='Test Foods Ltd', vat_number='GB123456789')
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.api = APIClient()
        self.api.force_authenticate(self.staff)

    def start(self, api=None):
        response = (api or self.api).post(reverse('hmrc-connect'))
        return response, __import__('urllib.parse', fromlist=['parse_qs']).parse_qs(
            __import__('urllib.parse', fromlist=['urlparse']).urlparse(response.json()['authorize_url']).query
        )['state'][0]

    def finish(self, browser, state):
        with mock.patch.object(client.SESSION, 'post', return_value=answer(200, TOKENS)):
            return browser.get(reverse('hmrc-callback'), {'code': 'c', 'state': state})

    def test_connect_sets_a_short_httponly_cookie(self):
        response, _state = self.start()
        cookie = response.cookies['hmrc_oauth']
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertEqual(cookie['path'], '/accounting/hmrc/callback/')
        self.assertEqual(int(cookie['max-age']), 600)

    def test_another_browser_cannot_finish_my_sign_in(self):
        _response, state = self.start()
        stranger = APIClient()  # no cookie: e.g. someone else's browser given the HMRC link
        response = self.finish(stranger, state)
        self.assertEqual(response['Location'], '/dashboard/accounts?hmrc=error&reason=state')
        self.assertFalse(HmrcConnection.objects.exists())

    def test_the_browser_that_started_it_finishes_it(self):
        _response, state = self.start()
        response = self.finish(self.api, state)
        self.assertEqual(response['Location'], '/dashboard/accounts?hmrc=connected')
        self.assertTrue(HmrcConnection.objects.exists())

    def test_a_user_no_longer_staff_cannot_finish(self):
        _response, state = self.start()
        get_user_model().objects.filter(pk=self.staff.pk).update(is_staff=False)
        self.assertEqual(self.finish(self.api, state)['Location'], '/dashboard/accounts?hmrc=error&reason=state')
        self.assertFalse(HmrcConnection.objects.exists())

    def test_refresh_keeps_the_grants_expiry(self):
        _response, state = self.start()
        self.finish(self.api, state)
        connection = HmrcConnection.objects.get()
        granted = connection.refresh_expires_at
        HmrcConnection.objects.update(access_expires_at=timezone.now())
        with mock.patch.object(client.SESSION, 'post', return_value=answer(200, TOKENS)):
            client.access_token(HmrcConnection.objects.get())
        self.assertEqual(HmrcConnection.objects.get().refresh_expires_at, granted)

    def test_callback_is_throttled(self):
        with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {'hmrc_callback': '2/min'}):
            from django.core.cache import cache
            cache.clear()
            codes = [self.client.get(reverse('hmrc-callback'), {'state': 'x'}).status_code for _ in range(3)]
        self.assertEqual(codes, [302, 302, 429])
        self.assertFalse(HmrcAuthState.objects.exists())
