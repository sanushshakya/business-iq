# authentication/tests.py

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
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
