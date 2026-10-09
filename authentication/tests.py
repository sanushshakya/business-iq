# authentication/tests.py

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from authentication.models import UserInvitation
from authentication.serializers import LoginUserSerializer
from tenants.models import Company

User = get_user_model()


class PasswordResetConfirmTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email='alice@example.com', password='old-password-1')
        self.url = reverse('password_reset_confirm')

    def payload(self, **overrides):
        data = {
            'uidb64': urlsafe_base64_encode(force_bytes(self.user.pk)),
            'token': default_token_generator.make_token(self.user),
            'new_password1': 'new-password-1',
            'new_password2': 'new-password-1',
        }
        data.update(overrides)
        return data

    def test_valid_token_resets_password(self):
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('new-password-1'))

    def test_invalid_token_is_rejected(self):
        response = self.client.post(self.url, self.payload(token='bad-token'))
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('old-password-1'))

    def test_unknown_user_is_rejected(self):
        uid = urlsafe_base64_encode(force_bytes(999999))
        response = self.client.post(self.url, self.payload(uidb64=uid))
        self.assertEqual(response.status_code, 400)

    def test_mismatched_passwords_are_rejected(self):
        response = self.client.post(self.url, self.payload(new_password2='different-pass-1'))
        self.assertEqual(response.status_code, 400)

    def test_missing_fields_are_rejected(self):
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 400)


class AcceptInvitationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse('accept-invitation')
        self.company = Company.objects.create(name='Test Co', registration_number='R1', address='Somewhere')

    def invite(self, **overrides):
        fields = {
            'company': self.company,
            'invited_email': 'new@example.com',
            'role': 'Owner',
            'token': uuid.uuid4(),
            'expires_at': timezone.now() + timedelta(hours=1),
        }
        fields.update(overrides)
        return UserInvitation.objects.create(**fields)

    def accept(self, invitation, **extra):
        data = {'token': str(invitation.token), 'password': 'pw-12345678'}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_valid_token_creates_user_and_marks_accepted(self):
        invitation = self.invite()
        response = self.accept(invitation)
        self.assertEqual(response.status_code, 201)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        user = User.objects.get(email='new@example.com')
        self.assertEqual(user.company, self.company)
        self.assertTrue(user.groups.filter(name='Owner').exists())

    def test_expired_token_is_rejected(self):
        invitation = self.invite(expires_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.accept(invitation).status_code, 400)
        self.assertFalse(User.objects.filter(email='new@example.com').exists())

    def test_token_cannot_be_reused(self):
        invitation = self.invite()
        self.assertEqual(self.accept(invitation).status_code, 201)
        self.assertEqual(self.accept(invitation).status_code, 400)

    def test_malformed_token_is_rejected(self):
        response = self.client.post(self.url, {'token': 'not-a-uuid', 'password': 'y'})
        self.assertEqual(response.status_code, 400)

    def test_missing_token_is_rejected(self):
        self.assertEqual(self.client.post(self.url, {}).status_code, 400)

    def test_missing_password_is_rejected(self):
        invitation = self.invite()
        response = self.client.post(self.url, {'token': str(invitation.token)})
        self.assertEqual(response.status_code, 400)


class LoginUserSerializerTests(TestCase):
    def test_valid_credentials(self):
        User.objects.create_user(email='carol@example.com', password='pw-12345678')
        serializer = LoginUserSerializer(data={'username': 'carol@example.com', 'password': 'pw-12345678'})
        self.assertTrue(serializer.is_valid())

    def test_invalid_credentials(self):
        serializer = LoginUserSerializer(data={'username': 'nobody', 'password': 'nope'})
        self.assertFalse(serializer.is_valid())


class LoginAndJwtTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # login is rate limited per client
        self.company_a = Company.objects.create(name='A', registration_number='JA', address='x')
        self.company_b = Company.objects.create(name='B', registration_number='JB', address='x')
        self.user = User.objects.create_user(email='a@example.com', password='pw-12345678', company=self.company_a)
        self.client = APIClient()
        self.login_url = reverse('login')

    def login(self, **overrides):
        data = {'username': 'a@example.com', 'password': 'pw-12345678'}
        data.update(overrides)
        return self.client.post(self.login_url, data)

    def test_login_returns_a_usable_bearer_token(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['token_type'], 'Bearer')
        self.assertEqual(body['expires_in'], 3600)

        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access_token']}")
        self.assertEqual(api.get(reverse('product-list')).status_code, 200)

    def test_token_is_scoped_to_the_users_company(self):
        from inventory.models import Product

        for company, name in ((self.company_a, 'mine'), (self.company_b, 'theirs')):
            Product.objects.create(company=company, name=name, description='d', price=1)
        token = self.login().json()['access_token']
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        names = [p['name'] for p in api.get(reverse('product-list')).json()['results']]
        self.assertEqual(names, ['mine'])

    def test_bad_credentials(self):
        self.assertEqual(self.login(password='wrong').status_code, 400)
        self.assertEqual(self.login(username='nobody@example.com').status_code, 400)
        self.assertEqual(self.client.post(self.login_url, {}).status_code, 400)

    def test_login_is_rate_limited(self):
        statuses = [self.login(password='wrong').status_code for _ in range(12)]
        self.assertEqual(statuses[:10], [400] * 10)
        self.assertIn(429, statuses[10:])

    def _call_with(self, token):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        return api.get(reverse('product-list'))

    def test_garbage_and_missing_tokens_are_rejected(self):
        self.assertEqual(self._call_with('garbage').status_code, 401)
        self.assertEqual(APIClient().get(reverse('product-list')).status_code, 401)  # no credentials at all

    def test_expired_token_is_rejected(self):
        from datetime import timedelta
        from unittest import mock

        from django.utils import timezone

        with mock.patch('authentication.jwt_handler.timezone.now', return_value=timezone.now() - timedelta(hours=2)):
            token = self.login().json()['access_token']
        self.assertEqual(self._call_with(token).status_code, 401)

    def test_token_signed_with_another_key_is_rejected(self):
        import jwt

        forged = jwt.encode({'user_id': self.user.pk, 'exp': 9999999999}, 'not-the-secret-key-not-the-secret-key', algorithm='HS256')
        self.assertEqual(self._call_with(forged).status_code, 401)

    def test_token_without_expiry_is_rejected(self):
        import jwt
        from django.conf import settings

        forever = jwt.encode({'user_id': self.user.pk}, settings.SECRET_KEY, algorithm='HS256')
        self.assertEqual(self._call_with(forever).status_code, 401)

    def test_deactivated_user_loses_access_immediately(self):
        token = self.login().json()['access_token']
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self._call_with(token).status_code, 401)
        self.assertEqual(self.login().status_code, 400)

    def test_unknown_user_in_token_is_rejected(self):
        token = self.login().json()['access_token']
        self.user.delete()
        self.assertEqual(self._call_with(token).status_code, 401)


@override_settings(JWT_REFRESH_REUSE_LEEWAY_SECONDS=0)  # strict: any reuse is treated as theft
class RefreshTokenTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.company = Company.objects.create(name='R', registration_number='RR', address='x')
        self.user = User.objects.create_user(email='r@example.com', password='pw-12345678', company=self.company)
        self.client = APIClient()
        self.refresh_url = reverse('token-refresh')
        self.logout_url = reverse('logout')

    def login(self):
        return self.client.post(reverse('login'), {'username': 'r@example.com', 'password': 'pw-12345678'}).json()

    def refresh(self, token):
        return self.client.post(self.refresh_url, {'refresh_token': token})

    def api_status(self, access_token):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        return api.get(reverse('product-list')).status_code

    # ---- login / refresh
    def test_login_returns_both_tokens(self):
        body = self.login()
        self.assertTrue(body['refresh_token'])
        self.assertEqual(body['refresh_expires_in'], 14 * 24 * 3600)
        self.assertEqual(self.api_status(body['access_token']), 200)

    def test_refresh_returns_a_working_new_pair(self):
        first = self.login()
        response = self.refresh(first['refresh_token'])
        self.assertEqual(response.status_code, 200)
        second = response.json()
        self.assertNotEqual(second['refresh_token'], first['refresh_token'])
        self.assertEqual(self.api_status(second['access_token']), 200)

    def test_refresh_tokens_are_single_use_and_stay_in_one_family(self):
        from authentication.models import RefreshToken

        first = self.login()
        second = self.refresh(first['refresh_token']).json()
        self.assertEqual(RefreshToken.objects.values('family').distinct().count(), 1)
        self.assertEqual(self.refresh(first['refresh_token']).status_code, 401)
        self.assertEqual(second['token_type'], 'Bearer')

    def test_replaying_a_used_token_revokes_the_whole_session(self):
        first = self.login()
        second = self.refresh(first['refresh_token']).json()
        self.assertEqual(self.refresh(first['refresh_token']).status_code, 401)  # replay (e.g. attacker)
        self.assertEqual(self.refresh(second['refresh_token']).status_code, 401)  # legit holder is cut off too

    def test_sessions_are_independent(self):
        phone, laptop = self.login(), self.login()
        self.assertEqual(self.refresh(phone['refresh_token']).status_code, 200)
        self.refresh(phone['refresh_token'])  # replay on the phone session
        self.assertEqual(self.refresh(laptop['refresh_token']).status_code, 200)

    def test_invalid_requests(self):
        self.assertEqual(self.refresh('not-a-real-token').status_code, 401)
        self.assertEqual(self.client.post(self.refresh_url, {}).status_code, 400)
        self.assertEqual(self.refresh(self.login()['access_token']).status_code, 401)  # access token is not a refresh token

    def test_refresh_token_is_not_accepted_as_an_access_token(self):
        self.assertEqual(self.api_status(self.login()['refresh_token']), 401)

    def test_expired_refresh_token(self):
        from datetime import timedelta

        from django.utils import timezone

        from authentication.models import RefreshToken

        body = self.login()
        RefreshToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.refresh(body['refresh_token']).status_code, 401)

    def test_deactivated_user_cannot_refresh(self):
        body = self.login()
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.refresh(body['refresh_token']).status_code, 401)

    def test_only_a_hash_of_the_token_is_stored(self):
        from authentication.models import RefreshToken

        body = self.login()
        stored = RefreshToken.objects.get()
        self.assertNotEqual(stored.token_hash, body['refresh_token'])
        self.assertEqual(len(stored.token_hash), 64)

    # ---- password changes
    def test_password_change_invalidates_access_and_refresh_tokens(self):
        body = self.login()
        self.user.set_password('a-different-pw-123')
        self.user.save()
        self.assertEqual(self.api_status(body['access_token']), 401)
        self.assertEqual(self.refresh(body['refresh_token']).status_code, 401)

    def test_password_reset_endpoint_revokes_all_sessions(self):
        from authentication.models import RefreshToken

        body, other = self.login(), self.login()
        response = self.client.post(reverse('password_reset_confirm'), {
            'uidb64': urlsafe_base64_encode(force_bytes(self.user.pk)),
            'token': default_token_generator.make_token(self.user),
            'new_password1': 'reset-password-1', 'new_password2': 'reset-password-1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RefreshToken.objects.filter(revoked_at__isnull=True).exists())
        for pair in (body, other):
            self.assertEqual(self.refresh(pair['refresh_token']).status_code, 401)
            self.assertEqual(self.api_status(pair['access_token']), 401)

    # ---- logout
    def test_logout_ends_only_that_session(self):
        phone, laptop = self.login(), self.login()
        self.assertEqual(self.client.post(self.logout_url, {'refresh_token': phone['refresh_token']}).status_code, 204)
        self.assertEqual(self.refresh(phone['refresh_token']).status_code, 401)
        self.assertEqual(self.refresh(laptop['refresh_token']).status_code, 200)

    def test_logout_covers_the_rotated_token_too(self):
        first = self.login()
        second = self.refresh(first['refresh_token']).json()
        self.client.post(self.logout_url, {'refresh_token': second['refresh_token']})
        self.assertEqual(self.refresh(second['refresh_token']).status_code, 401)

    def test_logout_is_idempotent_and_does_not_reveal_unknown_tokens(self):
        self.assertEqual(self.client.post(self.logout_url, {'refresh_token': 'whatever'}).status_code, 204)
        self.assertEqual(self.client.post(self.logout_url, {}).status_code, 400)

    # ---- housekeeping
    def test_refresh_is_rate_limited(self):
        statuses = [self.refresh('guess').status_code for _ in range(32)]
        self.assertEqual(statuses[:30], [401] * 30)
        self.assertIn(429, statuses[30:])

    def test_prune_removes_only_old_dead_tokens(self):
        from datetime import timedelta

        from django.utils import timezone

        from authentication.models import RefreshToken
        from authentication.tasks import prune_refresh_tokens

        live = self.login()
        long_ago = timezone.now() - timedelta(days=30)
        old_expired = self.login()
        old_revoked = self.login()
        recently_revoked = self.login()
        stored = {t.family: t for t in RefreshToken.objects.all()}
        self.assertEqual(len(stored), 4)
        tokens = list(RefreshToken.objects.order_by('pk'))
        tokens[1].expires_at = long_ago
        tokens[1].save()
        tokens[2].revoked_at = long_ago
        tokens[2].save()
        tokens[3].revoked_at = timezone.now()
        tokens[3].save()

        self.assertEqual(prune_refresh_tokens(), 2)
        self.assertEqual(sorted(RefreshToken.objects.values_list('pk', flat=True)), [tokens[0].pk, tokens[3].pk])
        self.assertEqual(self.refresh(live['refresh_token']).status_code, 200)
        self.assertTrue(old_expired and old_revoked and recently_revoked)

    def test_deleting_a_user_removes_their_refresh_tokens(self):
        from authentication.models import RefreshToken

        self.login()
        self.user.delete()
        self.assertFalse(RefreshToken.objects.exists())


class AllowedHostsParsingTests(TestCase):
    def test_commas_and_whitespace_are_both_accepted(self):
        import importlib
        import os
        from unittest import mock

        for raw in ('localhost,127.0.0.1', 'localhost 127.0.0.1', 'localhost, 127.0.0.1  [::1]'):
            with mock.patch.dict(os.environ, {'ALLOWED_HOSTS': raw}):
                module = importlib.import_module('config.settings')
                importlib.reload(module)
                self.assertTrue(all(' ' not in h and ',' not in h for h in module.ALLOWED_HOSTS), raw)
                self.assertIn('localhost', module.ALLOWED_HOSTS)
        importlib.reload(importlib.import_module('config.settings'))


class RefreshTokenSessionLimitTests(TestCase):
    """The reuse allowance for racing requests, and the hard cap on a session's lifetime."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.company = Company.objects.create(name='L', registration_number='LL', address='x')
        self.user = User.objects.create_user(email='l@example.com', password='pw-12345678', company=self.company)
        self.client = APIClient()

    def login(self):
        return self.client.post(reverse('login'), {'username': 'l@example.com', 'password': 'pw-12345678'}).json()

    def refresh(self, token):
        return self.client.post(reverse('token-refresh'), {'refresh_token': token})

    def api_status(self, access_token):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        return api.get(reverse('product-list')).status_code

    def age_session(self, **delta):
        """Pretend the login behind these tokens happened ``delta`` ago (the tokens themselves stay valid)."""
        from datetime import timedelta

        from django.db.models import F

        from authentication.models import RefreshToken

        RefreshToken.objects.update(session_started_at=F('session_started_at') - timedelta(**delta))

    # ---- two requests racing with the same token
    def test_a_token_used_twice_at_once_gets_a_second_working_pair(self):
        first = self.login()
        winner = self.refresh(first['refresh_token'])
        loser = self.refresh(first['refresh_token'])  # e.g. a second browser tab
        self.assertEqual((winner.status_code, loser.status_code), (200, 200))
        self.assertNotEqual(winner.json()['refresh_token'], loser.json()['refresh_token'])
        for response in (winner, loser):
            self.assertEqual(self.api_status(response.json()['access_token']), 200)
            self.assertEqual(self.refresh(response.json()['refresh_token']).status_code, 200)

    def test_the_allowance_expires(self):
        from datetime import timedelta

        from django.utils import timezone

        from authentication.models import RefreshToken

        first, = [self.login()]
        newest = self.refresh(first['refresh_token']).json()['refresh_token']
        RefreshToken.objects.filter(rotated_at__isnull=False).update(rotated_at=timezone.now() - timedelta(seconds=60))
        self.assertEqual(self.refresh(first['refresh_token']).status_code, 401)  # now it is a replay
        self.assertEqual(self.refresh(newest).status_code, 401)                  # and the whole session is gone

    @override_settings(JWT_REFRESH_REUSE_LEEWAY_SECONDS=0)
    def test_the_allowance_can_be_switched_off(self):
        first = self.login()
        self.refresh(first['refresh_token'])
        self.assertEqual(self.refresh(first['refresh_token']).status_code, 401)

    def test_the_allowance_never_revives_a_logged_out_session(self):
        first = self.login()
        newest = self.refresh(first['refresh_token']).json()['refresh_token']
        self.client.post(reverse('logout'), {'refresh_token': newest})
        self.assertEqual(self.refresh(first['refresh_token']).status_code, 401)

    def test_the_allowance_never_revives_a_session_after_a_password_change(self):
        first = self.login()
        self.refresh(first['refresh_token'])
        self.user.set_password('a-different-pw-123')
        self.user.save()
        self.assertEqual(self.refresh(first['refresh_token']).status_code, 401)

    # ---- hard cap on a session
    def test_a_session_cannot_be_refreshed_forever(self):
        body = self.login()
        for _ in range(3):  # a diligent client keeps rotating, so each token is young...
            body = self.refresh(body['refresh_token']).json()
        self.age_session(days=91)  # ...but the session itself started 91 days ago
        self.assertEqual(self.refresh(body['refresh_token']).status_code, 401)

    def test_the_session_start_is_carried_across_rotations(self):
        from authentication.models import RefreshToken

        body = self.login()
        started = RefreshToken.objects.get().session_started_at
        self.refresh(body['refresh_token'])
        self.assertEqual(set(RefreshToken.objects.values_list('session_started_at', flat=True)), {started})

    def test_a_token_never_outlives_the_session_cap(self):
        body = self.login()
        self.age_session(days=89, hours=23)  # one hour of the 90 day session is left
        refreshed = self.refresh(body['refresh_token'])
        self.assertEqual(refreshed.status_code, 200)
        self.assertLessEqual(refreshed.json()['refresh_expires_in'], 3600)
        self.assertGreater(refreshed.json()['refresh_expires_in'], 0)

    @override_settings(JWT_REFRESH_SESSION_MAX_AGE_SECONDS=3600)
    def test_the_cap_is_configurable(self):
        body = self.login()
        self.assertLessEqual(body['refresh_expires_in'], 3600)
        self.age_session(hours=2)
        self.assertEqual(self.refresh(body['refresh_token']).status_code, 401)



class InvitationApiTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.company = Company.objects.create(name='Inviter Co', registration_number='INV-1', address='x')
        self.other = Company.objects.create(name='Other Co', registration_number='INV-2', address='x')
        self.staff = User.objects.create_user(email='boss@example.com', password='pw-12345678', company=self.company, is_staff=True)
        self.member = User.objects.create_user(email='member@example.com', password='pw-12345678', company=self.company)
        self.client = APIClient()
        self.client.force_authenticate(self.staff)
        self.url = reverse('invitation-list')

    def invite(self, email='new@example.com', role='Buyer', client=None):
        return (client or self.client).post(self.url, {'invited_email': email, 'role': role})

    def test_staff_invite_by_email_and_the_token_never_appears_in_the_response(self):
        from django.core import mail

        response = self.invite()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body['invited_email'], body['role'], body['status'], body['email_sent']), ('new@example.com', 'Buyer', 'pending', True))
        self.assertNotIn('token', body)

        invitation = UserInvitation.objects.get()
        self.assertEqual(invitation.company, self.company)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['new@example.com'])
        self.assertIn(str(invitation.token), mail.outbox[0].body)
        self.assertIn('Inviter Co', mail.outbox[0].subject)

    def test_the_full_flow_from_invitation_email_to_a_working_login(self):
        import re

        from django.core import mail

        self.invite(email='newhire@example.com', role='Buyer')
        token = re.search(r'token=([0-9a-f-]{36})', mail.outbox[0].body).group(1)

        anonymous = APIClient()
        accepted = anonymous.post(reverse('accept-invitation'), {'token': token, 'password': 'a-good-password-1'})
        self.assertEqual(accepted.status_code, 201, accepted.content)

        login = anonymous.post(reverse('login'), {'username': 'newhire@example.com', 'password': 'a-good-password-1'})
        self.assertEqual(login.status_code, 200)
        user = User.objects.get(email='newhire@example.com')
        self.assertEqual(user.company, self.company)
        self.assertTrue(user.groups.filter(name='Buyer').exists())
        self.assertEqual(self.client.get(self.url).json()['results'][0]['status'], 'accepted')

    def test_only_company_staff_can_invite_or_list(self):
        member_client = APIClient()
        member_client.force_authenticate(self.member)
        self.assertEqual(self.invite(client=member_client).status_code, 403)
        self.assertEqual(member_client.get(self.url).status_code, 403)
        self.assertEqual(APIClient().get(self.url).status_code, 401)
        self.assertFalse(UserInvitation.objects.exists())

    def test_cannot_invite_twice_while_pending_or_after_acceptance(self):
        self.assertEqual(self.invite().status_code, 201)
        again = self.invite()
        self.assertEqual(again.status_code, 400)
        self.assertIn('invited_email', again.json())
        self.assertEqual(UserInvitation.objects.count(), 1)

    def test_an_expired_invitation_can_be_reissued_with_a_new_token(self):
        from datetime import timedelta

        from django.core import mail
        from django.utils import timezone

        self.invite()
        old = UserInvitation.objects.get()
        old_token = old.token
        UserInvitation.objects.update(expires_at=timezone.now() - timedelta(days=1))

        response = self.invite(role='Manager')
        self.assertEqual(response.status_code, 201, response.content)
        refreshed = UserInvitation.objects.get()
        self.assertEqual(refreshed.pk, old.pk)
        self.assertNotEqual(refreshed.token, old_token)
        self.assertEqual((refreshed.role, response.json()['status']), ('Manager', 'pending'))
        self.assertGreater(refreshed.expires_at, timezone.now())
        self.assertEqual(len(mail.outbox), 2)

        # the stale link no longer works
        stale = APIClient().post(reverse('accept-invitation'), {'token': str(old_token), 'password': 'a-good-password-1'})
        self.assertEqual(stale.status_code, 400)

    def test_existing_accounts_cannot_be_invited_and_the_answer_does_not_say_why(self):
        for email in ('member@example.com', 'MEMBER@example.com'):
            response = self.invite(email=email)
            self.assertEqual(response.status_code, 400)
        elsewhere = User.objects.create_user(email='elsewhere@example.com', password='pw-12345678', company=self.other)
        reply_for_other_company = self.invite(email=elsewhere.email).json()
        reply_for_nobody = self.invite(email='nobody-at-all@example.com')
        self.assertEqual(reply_for_other_company, {'invited_email': ['This email address cannot be invited.']})
        self.assertEqual(reply_for_nobody.status_code, 201)

    def test_the_same_address_can_be_invited_by_two_companies(self):
        other_staff = User.objects.create_user(email='boss2@example.com', password='pw-12345678', company=self.other, is_staff=True)
        other_client = APIClient()
        other_client.force_authenticate(other_staff)
        self.assertEqual(self.invite(email='shared@example.com').status_code, 201)
        self.assertEqual(self.invite(email='shared@example.com', client=other_client).status_code, 201)
        self.assertEqual(UserInvitation.objects.count(), 2)

    def test_invitations_are_private_to_their_company_and_can_be_revoked(self):
        theirs = UserInvitation.objects.create(
            company=self.other, invited_email='x@example.com', role='Buyer', token=uuid.uuid4(),
            expires_at=timezone_now() + timedelta_days(3))
        self.invite()
        mine = UserInvitation.objects.get(company=self.company)
        self.assertEqual([i['invited_email'] for i in self.client.get(self.url).json()['results']], ['new@example.com'])
        self.assertEqual(self.client.get(reverse('invitation-detail', args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.delete(reverse('invitation-detail', args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.delete(reverse('invitation-detail', args=[mine.pk])).status_code, 204)
        self.assertTrue(UserInvitation.objects.filter(pk=theirs.pk).exists())

    def test_a_revoked_invitation_cannot_be_accepted(self):
        self.invite()
        token = str(UserInvitation.objects.get().token)
        UserInvitation.objects.all().delete()
        response = APIClient().post(reverse('accept-invitation'), {'token': token, 'password': 'a-good-password-1'})
        self.assertEqual(response.status_code, 400)

    def test_invalid_input_is_rejected(self):
        for payload in ({}, {'invited_email': 'not-an-email', 'role': 'Buyer'}, {'invited_email': 'a@example.com'},
                        {'invited_email': 'a@example.com', 'role': '<script>'}):
            self.assertEqual(self.client.post(self.url, payload).status_code, 400, payload)

    def test_a_mail_outage_does_not_lose_the_invitation(self):
        from unittest import mock

        with mock.patch('authentication.emails.send_mail', side_effect=OSError('smtp down')):
            response = self.invite()
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json()['email_sent'])
        self.assertEqual(UserInvitation.objects.count(), 1)

    def test_users_without_a_company_cannot_invite(self):
        root = User.objects.create_superuser(email='root@example.com', password='pw-12345678')
        client = APIClient()
        client.force_authenticate(root)
        self.assertEqual(self.invite(client=client).status_code, 400)


def timezone_now():
    from django.utils import timezone

    return timezone.now()


def timedelta_days(days):
    from datetime import timedelta

    return timedelta(days=days)


class PasswordResetRequestTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.company = Company.objects.create(name='Reset Co', registration_number='RST-1', address='x')
        self.user = User.objects.create_user(email='forgetful@example.com', password='old-password-1', company=self.company)
        self.url = reverse('password_reset_request')
        self.client = APIClient()

    def test_the_emailed_link_resets_the_password_end_to_end(self):
        import re

        from django.core import mail

        response = self.client.post(self.url, {'email': 'forgetful@example.com'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        uid, token = re.search(r'uid=([^&\s]+)&token=([^\s]+)', mail.outbox[0].body).groups()

        confirm = self.client.post(reverse('password_reset_confirm'), {
            'uidb64': uid, 'token': token, 'new_password1': 'brand-new-pass-1', 'new_password2': 'brand-new-pass-1'})
        self.assertEqual(confirm.status_code, 200, confirm.content)
        login = self.client.post(reverse('login'), {'username': 'forgetful@example.com', 'password': 'brand-new-pass-1'})
        self.assertEqual(login.status_code, 200)
        # the link is single use: the password hash changed, so the token no longer verifies
        again = self.client.post(reverse('password_reset_confirm'), {
            'uidb64': uid, 'token': token, 'new_password1': 'another-pass-123', 'new_password2': 'another-pass-123'})
        self.assertEqual(again.status_code, 400)

    def test_the_answer_is_identical_for_unknown_and_inactive_addresses(self):
        from django.core import mail

        known = self.client.post(self.url, {'email': 'forgetful@example.com'})
        unknown = self.client.post(self.url, {'email': 'nobody@example.com'})
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        inactive = self.client.post(self.url, {'email': 'forgetful@example.com'})
        self.assertEqual((known.status_code, unknown.status_code, inactive.status_code), (200, 200, 200))
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(known.json(), inactive.json())
        self.assertEqual(len(mail.outbox), 1)  # only the first, genuine request sent anything

    def test_email_matching_ignores_case(self):
        from django.core import mail

        self.client.post(self.url, {'email': 'FORGETFUL@EXAMPLE.COM'})
        self.assertEqual(len(mail.outbox), 1)

    def test_invalid_input_and_rate_limit(self):
        self.assertEqual(self.client.post(self.url, {'email': 'nope'}).status_code, 400)
        self.assertEqual(self.client.post(self.url, {}).status_code, 400)
        statuses = [self.client.post(self.url, {'email': f'x{i}@example.com'}).status_code for i in range(7)]
        self.assertIn(429, statuses)

    def test_a_mail_outage_does_not_change_the_answer(self):
        from unittest import mock

        with mock.patch('authentication.emails.send_mail', side_effect=OSError('smtp down')):
            self.assertEqual(self.client.post(self.url, {'email': 'forgetful@example.com'}).status_code, 200)
