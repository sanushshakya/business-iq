from django.apps import apps
from django.contrib import admin
from django.test import TestCase

from authentication.models import RefreshToken
from common.tests.factories import UserFactory

# Registered through another model's admin (an inline on EventAdmin).
INLINE_ONLY = {'demand_calendar.DemandAlert'}
DJANGO_APPS = {'admin', 'auth', 'contenttypes', 'sessions'}


class AdminCoverageTests(TestCase):
    def test_every_model_is_in_the_admin(self):
        missing = [
            m._meta.label for m in apps.get_models()
            if m._meta.app_label not in DJANGO_APPS
            and m._meta.label not in INLINE_ONLY
            and m not in admin.site._registry
        ]
        self.assertEqual(missing, [])

    def test_refresh_tokens_are_superuser_only_and_read_only(self):
        model_admin = admin.site._registry[RefreshToken]
        staff = UserFactory(is_staff=True)
        superuser = UserFactory(is_staff=True, is_superuser=True)

        class Req:
            pass

        request = Req()
        request.user = staff
        self.assertFalse(model_admin.has_module_permission(request))
        request.user = superuser
        self.assertTrue(model_admin.has_module_permission(request))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertNotIn('token_hash', model_admin.get_fields(request))
