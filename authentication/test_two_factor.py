# authentication/test_two_factor.py

"""Two-factor sign-in (TOTP) for staff: models, service, endpoints and the admin reset."""

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
