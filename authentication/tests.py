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


class TenantMiddlewareTests(TestCase):
    def setUp(self):
        from django.test import RequestFactory
        from authentication.middleware import TenantMiddleware

        self.factory = RequestFactory()
        self.company = Company.objects.create(name='Mw Co', registration_number='R2', address='Here')
        self.middleware = TenantMiddleware(lambda request: request)

    def call(self, token=None):
        extra = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
        return self.middleware(self.factory.get('/', **extra))

    def encode(self, **payload):
        import jwt
        from django.conf import settings
        return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

    def test_valid_token_attaches_company(self):
        request = self.call(self.encode(company_id=self.company.id))
        self.assertEqual(request.company, self.company)

    def test_no_header_passes_through(self):
        self.assertFalse(hasattr(self.call(), 'company'))

    def test_invalid_token_is_401(self):
        self.assertEqual(self.call('garbage').status_code, 401)

    def test_missing_company_claim_is_401(self):
        self.assertEqual(self.call(self.encode(sub='x')).status_code, 401)

    def test_unknown_company_is_404(self):
        self.assertEqual(self.call(self.encode(company_id=99999)).status_code, 404)
