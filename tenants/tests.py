# tenants/tests.py

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from tenants.models import Company


class CustomUserTests(TestCase):
    def test_create_user_requires_email(self):
        with self.assertRaises(ValueError):
            get_user_model().objects.create_user(email='', password='x')

    def test_create_superuser_flags(self):
        user = get_user_model().objects.create_superuser(email='root@example.com', password='x')
        self.assertTrue(user.is_staff and user.is_superuser)


class SeedDemoDataTests(TestCase):
    def test_is_idempotent(self):
        for _ in range(2):
            call_command('seed_demo_data', stdout=StringIO())
        self.assertEqual(Company.objects.filter(registration_number='M18-DEMO').count(), 1)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(get_user_model().objects.get().company.name, 'M18 Foods Ltd')
