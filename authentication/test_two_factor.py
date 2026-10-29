# authentication/test_two_factor.py

"""Two-factor sign-in (TOTP) for staff: models, service, endpoints and the admin reset."""

from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tenancy import TENANT_LOOKUPS
from tenants.models import Company

User = get_user_model()


class TwoFactorEventAdminTests(TestCase):
    def setUp(self):
        from authentication.models import TwoFactorEvent

        self.model_admin = admin.site._registry[TwoFactorEvent]
        company = Company.objects.create(name='Fake Foods Ltd', registration_number='FAKE1')
        self.request = RequestFactory().get('/admin/')
        self.request.user = User.objects.create_user(
            email='boss@example.com', password='pw-12345678', company=company, is_staff=True
        )

    def test_two_factor_event_admin_is_read_only(self):
        self.assertFalse(self.model_admin.has_add_permission(self.request))
        self.assertFalse(self.model_admin.has_change_permission(self.request))
        self.assertFalse(self.model_admin.has_delete_permission(self.request))

    def test_two_factor_event_is_tenant_scoped(self):
        self.assertEqual(TENANT_LOOKUPS['authentication.TwoFactorEvent'], 'company')

    def test_secrets_are_not_registered_in_admin(self):
        from authentication.models import RecoveryCode, TwoFactorChallenge, TwoFactorDevice

        for model in (TwoFactorDevice, RecoveryCode, TwoFactorChallenge):
            self.assertNotIn(model, admin.site._registry)


T0 = 1_800_000_000  # a fixed moment, on a 30-second step boundary


def _code(secret, at):
    import pyotp

    return pyotp.TOTP(secret).at(at)


class TwoFactorServiceTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.company = Company.objects.create(name='Fake Foods Ltd', registration_number='FAKE1')
        self.user = User.objects.create_user(email='staff@example.com', password='pw-12345678', company=self.company)
        self.clock = mock.patch('authentication.two_factor._now', return_value=T0)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def at(self, moment):
        self.clock.stop()
        self.clock = mock.patch('authentication.two_factor._now', return_value=moment)
        self.clock.start()

    def enrol(self):
        from authentication import two_factor

        secret = two_factor.start_enrolment(self.user)['secret']
        codes = two_factor.confirm_enrolment(self.user, _code(secret, T0))
        self.at(T0 + 30)  # the confirming code's step is used up
        return secret, codes

    def test_enrolment_returns_secret_uri_and_svg(self):
        from authentication import two_factor

        data = two_factor.start_enrolment(self.user)
        self.assertEqual(len(data['secret']), 32)
        self.assertTrue(data['otpauth_uri'].startswith('otpauth://totp/Business%20IQ:staff%40example.com?'))
        self.assertIn('<svg', data['qr_svg'])
        # Shown through <img src="data:image/svg+xml,...">, which only renders a namespaced, standalone SVG.
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', data['qr_svg'])
        self.assertNotIn('<?xml', data['qr_svg'])
        self.assertFalse(two_factor.is_enabled(self.user))

    def test_confirm_gives_ten_recovery_codes_and_audit(self):
        from authentication import two_factor
        from authentication.models import TwoFactorEvent

        _secret, codes = self.enrol()
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        self.assertRegex(codes[0], r'^[a-z2-7]{5}-[a-z2-7]{5}$')
        self.assertTrue(two_factor.is_enabled(self.user))
        event = TwoFactorEvent.objects.get()
        self.assertEqual((event.kind, event.user, event.company), ('enrolled', self.user, self.company))

    def test_confirm_with_wrong_code_does_not_enable(self):
        from authentication import two_factor

        two_factor.start_enrolment(self.user)
        with self.assertRaises(two_factor.TwoFactorError):
            two_factor.confirm_enrolment(self.user, '000000')
        self.assertFalse(two_factor.is_enabled(self.user))

    def test_enrol_while_enabled_keeps_the_secret(self):
        from authentication import two_factor

        secret, _codes = self.enrol()
        with self.assertRaises(two_factor.TwoFactorError):
            two_factor.start_enrolment(self.user)
        self.user.two_factor.refresh_from_db()
        self.assertEqual(self.user.two_factor.secret, secret)

    def test_valid_code_accepted(self):
        from authentication import two_factor

        secret, _ = self.enrol()
        self.assertTrue(two_factor.check_code(self.user, _code(secret, T0 + 30)))

    def test_code_from_the_previous_step_accepted(self):
        from authentication import two_factor

        secret, _ = self.enrol()
        self.at(T0 + 65)
        self.assertTrue(two_factor.check_code(self.user, _code(secret, T0 + 30)))

    def test_code_with_spaces_accepted(self):
        from authentication import two_factor

        secret, _ = self.enrol()
        code = _code(secret, T0 + 30)
        self.assertTrue(two_factor.check_code(self.user, f' {code[:3]} {code[3:]} '))

    def test_same_code_twice_in_window_refused(self):
        from authentication import two_factor

        secret, _ = self.enrol()
        code = _code(secret, T0 + 30)
        self.assertTrue(two_factor.check_code(self.user, code))
        self.assertFalse(two_factor.check_code(self.user, code))

    def test_older_step_than_last_used_refused(self):
        from authentication import two_factor

        secret, _ = self.enrol()
        self.at(T0 + 60)
        self.assertTrue(two_factor.check_code(self.user, _code(secret, T0 + 60)))
        self.assertFalse(two_factor.check_code(self.user, _code(secret, T0 + 30)))  # still inside the window

    def test_lockout_after_ten_failures(self):
        from authentication import two_factor

        secret, codes = self.enrol()
        for _ in range(10):
            self.assertFalse(two_factor.check_code(self.user, '000000'))
        self.assertFalse(two_factor.check_code(self.user, _code(secret, T0 + 30)))
        self.assertFalse(two_factor.use_recovery_code(self.user, codes[0]))
        self.assertTrue(two_factor.is_locked(self.user))

    def test_success_clears_the_failure_count(self):
        from authentication import two_factor

        secret, _ = self.enrol()
        for _ in range(9):
            two_factor.check_code(self.user, '000000')
        self.assertTrue(two_factor.check_code(self.user, _code(secret, T0 + 30)))
        two_factor.check_code(self.user, '000000')
        self.assertFalse(two_factor.is_locked(self.user))

    def test_recovery_code_single_use(self):
        from authentication import two_factor
        from authentication.models import TwoFactorEvent

        _secret, codes = self.enrol()
        self.assertTrue(two_factor.use_recovery_code(self.user, codes[0]))
        self.assertFalse(two_factor.use_recovery_code(self.user, codes[0]))
        self.assertEqual(two_factor.recovery_codes_left(self.user), 9)
        self.assertTrue(TwoFactorEvent.objects.filter(kind='recovery_code_used', user=self.user).exists())

    def test_recovery_code_normalised(self):
        from authentication import two_factor

        _secret, codes = self.enrol()
        self.assertTrue(two_factor.use_recovery_code(self.user, codes[0].upper().replace('-', ' ')))

    def test_recovery_codes_stored_hashed(self):
        from authentication.models import RecoveryCode

        _secret, codes = self.enrol()
        stored = set(RecoveryCode.objects.values_list('code_hash', flat=True))
        self.assertFalse({c for c in codes} & stored)
        self.assertFalse({c.replace('-', '') for c in codes} & stored)

    def test_secret_not_stored_in_plain_text(self):
        from django.db import connection

        secret, _ = self.enrol()
        with connection.cursor() as cursor:
            cursor.execute('SELECT secret FROM authentication_twofactordevice')
            raw = cursor.fetchone()[0]
        self.assertNotIn(secret, raw)

    def test_operators_and_plain_staff_are_not_required(self):
        from authentication import two_factor

        operator, _code_ = User.objects.create_operator(self.company, 'Fake Till', 'till')
        admin_user = User.objects.create_user(email='admin@example.com', password='pw-12345678', is_staff=True)
        self.assertFalse(two_factor.is_required(self.user))
        self.assertFalse(two_factor.is_required(operator))
        self.assertTrue(two_factor.is_required(admin_user))

    def test_challenge_round_trip_and_single_use(self):
        from authentication import two_factor

        raw = two_factor.issue_challenge(self.user, 'verify')
        challenge = two_factor.load_challenge(raw, 'verify')
        self.assertEqual(challenge.user, self.user)
        with self.assertRaises(two_factor.InvalidChallenge):
            two_factor.load_challenge(raw, 'enrol')
        two_factor.consume(challenge)
        with self.assertRaises(two_factor.InvalidChallenge):
            two_factor.load_challenge(raw, 'verify')

    def test_challenge_expires(self):
        from datetime import timedelta

        from django.utils import timezone

        from authentication import two_factor

        raw = two_factor.issue_challenge(self.user, 'verify')
        with mock.patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(seconds=301)):
            with self.assertRaises(two_factor.InvalidChallenge):
                two_factor.load_challenge(raw, 'verify')

    def test_challenge_dead_after_five_failures(self):
        from authentication import two_factor

        raw = two_factor.issue_challenge(self.user, 'verify')
        for _ in range(5):
            two_factor.record_failure(two_factor.load_challenge(raw, 'verify'))
        with self.assertRaises(two_factor.InvalidChallenge):
            two_factor.load_challenge(raw, 'verify')

    def test_reset_removes_device_revokes_sessions_and_audits(self):
        from authentication import refresh_tokens, two_factor
        from authentication.models import TwoFactorEvent

        self.enrol()
        pair = refresh_tokens.issue_token_pair(self.user)
        boss = User.objects.create_user(email='boss@example.com', password='pw-12345678', company=self.company,
                                        is_staff=True)
        two_factor.reset(self.user, actor=boss)
        self.assertFalse(two_factor.is_enabled(self.user))
        with self.assertRaises(refresh_tokens.InvalidRefreshToken):
            refresh_tokens.rotate(pair['refresh_token'])
        event = TwoFactorEvent.objects.get(kind='reset')
        self.assertEqual((event.user, event.actor, event.company), (self.user, boss, self.company))

    def test_disable_needs_a_code_and_is_refused_when_required(self):
        from authentication import two_factor

        secret, _ = self.enrol()
        with self.assertRaises(two_factor.TwoFactorError):
            two_factor.disable(self.user, '000000')
        two_factor.disable(self.user, _code(secret, T0 + 30))
        self.assertFalse(two_factor.is_enabled(self.user))

        self.user.is_staff = True
        self.user.save()
        self.at(T0)
        secret, _ = self.enrol()
        with self.assertRaises(two_factor.TwoFactorError):
            two_factor.disable(self.user, _code(secret, T0 + 30))
        self.assertTrue(two_factor.is_enabled(self.user))


class TwoFactorApiTests(TestCase):
    """Sign-in step-up, enrolment, status and disabling over HTTP."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # these routes are rate limited per client
        self.company = Company.objects.create(name='Fake Foods Ltd', registration_number='FAKE1')
        self.user = User.objects.create_user(email='staff@example.com', password='pw-12345678', company=self.company)
        self.client = APIClient()
        self.clock = mock.patch('authentication.two_factor._now', return_value=T0)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def at(self, moment):
        self.clock.stop()
        self.clock = mock.patch('authentication.two_factor._now', return_value=moment)
        self.clock.start()

    def login(self, email='staff@example.com'):
        return self.client.post(reverse('login'), {'username': email, 'password': 'pw-12345678'})

    def enrol(self, user=None):
        from authentication import two_factor

        user = user or self.user
        secret = two_factor.start_enrolment(user)['secret']
        two_factor.confirm_enrolment(user, _code(secret, T0))
        self.at(T0 + 30)
        return secret

    def bearer(self, access_token):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        return api

    def test_non_admin_without_2fa_gets_tokens(self):
        body = self.login().json()
        self.assertIn('access_token', body)

    def test_enrolled_user_gets_no_tokens_without_valid_code(self):
        self.enrol()
        response = self.login()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['two_factor'], 'verify')
        self.assertNotIn('access_token', body)
        self.assertNotIn('refresh_token', body)

        wrong = self.client.post(reverse('two-factor-verify'), {'challenge': body['challenge'], 'code': '000000'})
        self.assertEqual(wrong.status_code, 400)
        self.assertNotIn('access_token', wrong.json())
        missing = self.client.post(reverse('two-factor-verify'), {'challenge': body['challenge']})
        self.assertEqual(missing.status_code, 400)
        self.assertNotIn('access_token', missing.json())

    def test_verify_with_valid_code_returns_tokens(self):
        secret = self.enrol()
        challenge = self.login().json()['challenge']
        response = self.client.post(
            reverse('two-factor-verify'), {'challenge': challenge, 'code': _code(secret, T0 + 30)}
        )
        self.assertEqual(response.status_code, 200)
        api = self.bearer(response.json()['access_token'])
        self.assertTrue(api.get(reverse('me')).json()['two_factor_enabled'])

    def test_verify_with_recovery_code_returns_tokens(self):
        from authentication import two_factor

        two_factor.start_enrolment(self.user)
        secret = self.user.two_factor.secret
        codes = two_factor.confirm_enrolment(self.user, _code(secret, T0))
        challenge = self.login().json()['challenge']
        response = self.client.post(reverse('two-factor-verify'), {'challenge': challenge, 'recovery_code': codes[3]})
        self.assertEqual(response.status_code, 200)
        self.assertIn('access_token', response.json())

    def test_challenge_cannot_be_reused(self):
        secret = self.enrol()
        challenge = self.login().json()['challenge']
        url = reverse('two-factor-verify')
        self.assertEqual(self.client.post(url, {'challenge': challenge, 'code': _code(secret, T0 + 30)}).status_code, 200)
        self.at(T0 + 60)
        again = self.client.post(url, {'challenge': challenge, 'code': _code(secret, T0 + 60)})
        self.assertEqual(again.status_code, 401)

    def test_challenge_dies_after_five_wrong_codes(self):
        secret = self.enrol()
        challenge = self.login().json()['challenge']
        url = reverse('two-factor-verify')
        for _ in range(5):
            self.assertEqual(self.client.post(url, {'challenge': challenge, 'code': '000000'}).status_code, 400)
        self.assertEqual(
            self.client.post(url, {'challenge': challenge, 'code': _code(secret, T0 + 30)}).status_code, 401
        )

    def test_admin_without_2fa_must_enrol_before_tokens(self):
        User.objects.create_user(email='admin@example.com', password='pw-12345678', company=self.company, is_staff=True)
        body = self.login('admin@example.com').json()
        self.assertEqual(body['two_factor'], 'enrol')
        self.assertNotIn('access_token', body)
        # An enrol challenge is not a verify challenge.
        verify = self.client.post(reverse('two-factor-verify'), {'challenge': body['challenge'], 'code': '000000'})
        self.assertEqual(verify.status_code, 401)

    def test_forced_enrolment_confirm_returns_tokens_and_codes(self):
        admin_user = User.objects.create_user(
            email='admin@example.com', password='pw-12345678', company=self.company, is_staff=True
        )
        challenge = self.login('admin@example.com').json()['challenge']
        start = self.client.post(reverse('two-factor-enrol'), {'challenge': challenge})
        self.assertEqual(start.status_code, 200)
        data = start.json()
        self.assertIn('<svg', data['qr_svg'])
        confirm = self.client.post(
            reverse('two-factor-enrol-confirm'), {'challenge': challenge, 'code': _code(data['secret'], T0)}
        )
        self.assertEqual(confirm.status_code, 200)
        body = confirm.json()
        self.assertEqual(len(body['recovery_codes']), 10)
        self.assertIn('access_token', body)
        admin_user.refresh_from_db()
        self.assertIsNotNone(admin_user.two_factor.confirmed_at)
        # The challenge is used up.
        again = self.client.post(reverse('two-factor-enrol'), {'challenge': challenge})
        self.assertEqual(again.status_code, 401)

    def test_signed_in_staff_can_enrol_optionally(self):
        api = self.bearer(self.login().json()['access_token'])
        self.assertEqual(api.get(reverse('two-factor-status')).json(),
                         {'enabled': False, 'required': False, 'recovery_codes_left': 0})
        secret = api.post(reverse('two-factor-enrol'), {}).json()['secret']
        confirm = api.post(reverse('two-factor-enrol-confirm'), {'code': _code(secret, T0)})
        self.assertEqual(confirm.status_code, 200)
        self.assertNotIn('access_token', confirm.json())
        self.assertEqual(api.get(reverse('two-factor-status')).json()['recovery_codes_left'], 10)

    def test_enrol_without_sign_in_or_challenge_is_401(self):
        self.assertEqual(self.client.post(reverse('two-factor-enrol'), {}).status_code, 401)

    def test_enrol_while_enabled_is_refused(self):
        secret = self.enrol()
        challenge = self.login().json()['challenge']
        tokens = self.client.post(
            reverse('two-factor-verify'), {'challenge': challenge, 'code': _code(secret, T0 + 30)}
        ).json()
        response = self.bearer(tokens['access_token']).post(reverse('two-factor-enrol'), {})
        self.assertEqual(response.status_code, 400)
        self.user.two_factor.refresh_from_db()
        self.assertEqual(self.user.two_factor.secret, secret)

    def test_staff_can_disable_with_code(self):
        from authentication import two_factor

        api = self.bearer(self.login().json()['access_token'])
        secret = self.enrol()
        self.assertEqual(api.post(reverse('two-factor-disable'), {'code': '000000'}).status_code, 400)
        response = api.post(reverse('two-factor-disable'), {'code': _code(secret, T0 + 30)})
        self.assertEqual(response.status_code, 204)
        self.assertFalse(two_factor.is_enabled(self.user))

    def test_admin_cannot_disable(self):
        from authentication import refresh_tokens

        admin_user = User.objects.create_user(
            email='admin@example.com', password='pw-12345678', company=self.company, is_staff=True
        )
        secret = self.enrol(admin_user)
        api = self.bearer(refresh_tokens.issue_token_pair(admin_user)['access_token'])
        response = api.post(reverse('two-factor-disable'), {'code': _code(secret, T0 + 30)})
        self.assertEqual(response.status_code, 400)

    def test_refresh_refused_for_admin_without_2fa(self):
        from authentication import refresh_tokens

        admin_user = User.objects.create_user(
            email='admin@example.com', password='pw-12345678', company=self.company, is_staff=True
        )
        pair = refresh_tokens.issue_token_pair(admin_user)  # a session from before 2FA was required
        response = self.client.post(reverse('token-refresh'), {'refresh_token': pair['refresh_token']})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('access_token', response.json())

    def test_refresh_still_works_for_enrolled_admin(self):
        from authentication import refresh_tokens

        admin_user = User.objects.create_user(
            email='admin@example.com', password='pw-12345678', company=self.company, is_staff=True
        )
        self.enrol(admin_user)
        pair = refresh_tokens.issue_token_pair(admin_user)
        response = self.client.post(reverse('token-refresh'), {'refresh_token': pair['refresh_token']})
        self.assertEqual(response.status_code, 200)

    def test_verify_is_rate_limited(self):
        self.enrol()
        challenge = self.login().json()['challenge']
        statuses = [
            self.client.post(reverse('two-factor-verify'), {'challenge': challenge, 'code': '000000'}).status_code
            for _ in range(11)
        ]
        self.assertEqual(statuses[-1], 429)

    def test_operator_is_forbidden_from_two_factor_routes(self):
        from authentication import refresh_tokens

        operator, _code_ = User.objects.create_operator(self.company, 'Fake Till', 'till')
        api = self.bearer(refresh_tokens.issue_token_pair(operator)['access_token'])
        self.assertEqual(api.get(reverse('two-factor-status')).status_code, 403)
        self.assertEqual(api.post(reverse('two-factor-enrol'), {}).status_code, 403)

    def test_schema_documents_two_factor_routes(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        paths = schema['paths']
        for path in ('/auth/2fa/', '/auth/2fa/verify/', '/auth/2fa/enrol/', '/auth/2fa/enrol/confirm/',
                     '/auth/2fa/disable/'):
            self.assertIn(path, paths)
        login = paths['/auth/login/']['post']['responses']['200']['content']['application/json']['schema']
        self.assertEqual(login['$ref'], '#/components/schemas/LoginResponse')
        self.assertIn('oneOf', schema['components']['schemas']['LoginResponse'])


class StaffTwoFactorResetTests(TestCase):
    """An admin lists the company's password users and resets someone's 2FA (lost phone)."""

    def setUp(self):
        from django.core.cache import cache

        from authentication import refresh_tokens, two_factor

        cache.clear()
        self.company = Company.objects.create(name='Fake Foods Ltd', registration_number='FAKE1')
        self.other_company = Company.objects.create(name='Other Fake Ltd', registration_number='FAKE2')
        self.boss = User.objects.create_user(email='boss@example.com', password='pw-12345678', company=self.company,
                                             is_staff=True)
        self.clerk = User.objects.create_user(email='clerk@example.com', password='pw-12345678', company=self.company)
        self.stranger = User.objects.create_user(email='stranger@example.com', password='pw-12345678',
                                                 company=self.other_company)
        User.objects.create_operator(self.company, 'Fake Till', 'till')
        with mock.patch('authentication.two_factor._now', return_value=T0):
            for user in (self.clerk, self.stranger):
                secret = two_factor.start_enrolment(user)['secret']
                two_factor.confirm_enrolment(user, _code(secret, T0))
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(self.boss)['access_token']}")

    def reset_url(self, user):
        return reverse('two-factor-user-reset', args=[user.pk])

    def test_lists_company_password_users_with_status(self):
        response = self.api.get(reverse('two-factor-user-list'))
        self.assertEqual(response.status_code, 200)
        rows = {row['email']: row for row in response.json()['results']}
        self.assertEqual(set(rows), {'boss@example.com', 'clerk@example.com'})
        self.assertTrue(rows['clerk@example.com']['two_factor_enabled'])
        self.assertFalse(rows['boss@example.com']['two_factor_enabled'])
        self.assertTrue(rows['boss@example.com']['is_staff'])

    def test_admin_resets_other_users_2fa_and_event_is_written(self):
        from authentication import two_factor
        from authentication.models import TwoFactorEvent

        response = self.api.post(self.reset_url(self.clerk))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(two_factor.is_enabled(self.clerk))
        event = TwoFactorEvent.objects.get(kind='reset')
        self.assertEqual((event.user, event.actor, event.company), (self.clerk, self.boss, self.company))

    def test_reset_revokes_target_sessions(self):
        from authentication import refresh_tokens

        pair = refresh_tokens.issue_token_pair(self.clerk)
        self.api.post(self.reset_url(self.clerk))
        response = APIClient().post(reverse('token-refresh'), {'refresh_token': pair['refresh_token']})
        self.assertEqual(response.status_code, 401)

    def test_reset_other_company_user_is_404(self):
        from authentication import two_factor

        self.assertEqual(self.api.post(self.reset_url(self.stranger)).status_code, 404)
        self.assertTrue(two_factor.is_enabled(self.stranger))

    def test_other_company_users_not_listed(self):
        emails = [row['email'] for row in self.api.get(reverse('two-factor-user-list')).json()['results']]
        self.assertNotIn('stranger@example.com', emails)

    def test_non_staff_gets_403(self):
        from authentication import refresh_tokens

        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(self.clerk)['access_token']}")
        self.assertEqual(api.get(reverse('two-factor-user-list')).status_code, 403)
        self.assertEqual(api.post(self.reset_url(self.boss)).status_code, 403)

    def test_operator_gets_403(self):
        from authentication import refresh_tokens

        operator = User.objects.get(role='till')
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(operator)['access_token']}")
        self.assertEqual(api.get(reverse('two-factor-user-list')).status_code, 403)

    def test_cannot_reset_self(self):
        self.assertEqual(self.api.post(self.reset_url(self.boss)).status_code, 400)

    def test_operator_cannot_be_reset(self):
        operator = User.objects.get(role='till')
        self.assertEqual(self.api.post(self.reset_url(operator)).status_code, 404)

    def test_schema_documents_reset_routes(self):
        from drf_spectacular.generators import SchemaGenerator

        paths = SchemaGenerator().get_schema(request=None, public=True)['paths']
        self.assertIn('/auth/2fa/users/', paths)
        self.assertIn('/auth/2fa/users/{id}/reset/', paths)


class TwoFactorHardeningTests(TestCase):
    """Fixes from the G06 review: no password-only way in, superusers protected, atomic reset, keyed hashes."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.company = Company.objects.create(name='Fake Foods Ltd', registration_number='FAKE1')
        self.other_company = Company.objects.create(name='Other Fake Ltd', registration_number='FAKE2')
        self.clock = mock.patch('authentication.two_factor._now', return_value=T0)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def user(self, email, **extra):
        return User.objects.create_user(email=email, password='pw-12345678', company=self.company, **extra)

    def enrol(self, user):
        from authentication import two_factor

        secret = two_factor.start_enrolment(user)['secret']
        codes = two_factor.confirm_enrolment(user, _code(secret, T0))
        self.clock.stop()
        self.clock = mock.patch('authentication.two_factor._now', return_value=T0 + 30)
        self.clock.start()
        return secret, codes

    def basic(self, email):
        import base64

        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION='Basic ' + base64.b64encode(f'{email}:pw-12345678'.encode()).decode())
        return api

    def test_basic_auth_still_works_for_plain_users(self):
        self.user('plain@example.com')
        self.assertEqual(self.basic('plain@example.com').get(reverse('me')).status_code, 200)

    def test_basic_auth_refused_for_admin(self):
        self.user('admin@example.com', is_staff=True)
        self.assertEqual(self.basic('admin@example.com').get(reverse('me')).status_code, 401)

    def test_basic_auth_refused_once_2fa_is_on(self):
        self.enrol(self.user('plain@example.com'))
        self.assertEqual(self.basic('plain@example.com').get(reverse('me')).status_code, 401)

    def admin_login(self, email, **extra):
        from django.test import Client

        client = Client()
        response = client.post(
            '/admin/login/?next=/admin/', {'username': email, 'password': 'pw-12345678', **extra}
        )
        return client, response

    def test_admin_login_page_has_the_code_field(self):
        from django.test import Client

        self.assertContains(Client().get('/admin/login/'), 'name="code"')

    def test_admin_site_needs_a_code_for_enrolled_staff(self):
        secret, _codes = self.enrol(self.user('admin@example.com', is_staff=True))
        client, response = self.admin_login('admin@example.com')
        self.assertEqual(response.status_code, 200)  # the form again, with an error
        self.assertNotIn('_auth_user_id', client.session)
        client, response = self.admin_login('admin@example.com', code='000000')
        self.assertNotIn('_auth_user_id', client.session)
        client, response = self.admin_login('admin@example.com', code=_code(secret, T0 + 30))
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', client.session)

    def test_admin_site_accepts_a_recovery_code(self):
        _secret, codes = self.enrol(self.user('admin@example.com', is_staff=True))
        client, response = self.admin_login('admin@example.com', code=codes[0])
        self.assertEqual(response.status_code, 302)

    def test_admin_site_refuses_admin_without_2fa(self):
        self.user('admin@example.com', is_staff=True)
        client, response = self.admin_login('admin@example.com')
        self.assertNotIn('_auth_user_id', client.session)
        self.assertContains(response, 'Set up two-factor sign-in')

    def test_staff_cannot_reset_a_superuser(self):
        from authentication import refresh_tokens, two_factor

        boss = self.user('boss@example.com', is_staff=True)
        root = self.user('root@example.com', is_staff=True, is_superuser=True)
        self.enrol(root)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(boss)['access_token']}")
        response = api.post(reverse('two-factor-user-reset', args=[root.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(two_factor.is_enabled(root))

    def test_reset_is_all_or_nothing(self):
        from authentication import two_factor
        from authentication.models import TwoFactorEvent

        target = self.user('clerk@example.com')
        boss = self.user('boss@example.com', is_staff=True)
        self.enrol(target)
        with mock.patch('authentication.refresh_tokens.revoke_all_for_user', side_effect=RuntimeError('db down')):
            with self.assertRaises(RuntimeError):
                two_factor.reset(target, actor=boss)
        self.assertTrue(two_factor.is_enabled(target))
        self.assertFalse(TwoFactorEvent.objects.filter(kind='reset').exists())

    def test_recovery_code_hash_is_keyed(self):
        import hashlib

        from authentication.models import RecoveryCode

        _secret, codes = self.enrol(self.user('plain@example.com'))
        plain = hashlib.sha256(codes[0].replace('-', '').encode()).hexdigest()
        self.assertFalse(RecoveryCode.objects.filter(code_hash=plain).exists())

    def test_audit_admin_hides_other_companies_events(self):
        from authentication.models import TwoFactorEvent

        mine = self.user('mine@example.com')
        theirs = User.objects.create_user(email='theirs@example.com', password='pw-12345678',
                                          company=self.other_company)
        TwoFactorEvent.objects.create(company=self.company, user=mine, kind='enrolled')
        TwoFactorEvent.objects.create(company=self.other_company, user=theirs, kind='enrolled')
        request = RequestFactory().get('/admin/')
        request.user = self.user('boss@example.com', is_staff=True)
        queryset = admin.site._registry[TwoFactorEvent].get_queryset(request)
        self.assertEqual([e.user.email for e in queryset], ['mine@example.com'])


class SchemaWarningTests(TestCase):
    def test_schema_generates_without_warnings(self):
        from io import StringIO

        from django.core.management import call_command

        err = StringIO()
        call_command('spectacular', '--file', '/dev/null', '--fail-on-warn', stderr=err)
