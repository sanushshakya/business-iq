# authentication/test_two_factor.py

"""Two-factor sign-in (TOTP) for staff: models, service, endpoints and the admin reset."""

from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from common.tenancy import TENANT_LOOKUPS
from tenants.models import Company

User = get_user_model()


class TwoFactorEventAdminTests(TestCase):
    def setUp(self):
        from authentication.models import TwoFactorEvent

        self.model_admin = admin.site._registry[TwoFactorEvent]
        company = Company.objects.create(name='Fake Foods Ltd')
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
        self.company = Company.objects.create(name='Fake Foods Ltd')
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
