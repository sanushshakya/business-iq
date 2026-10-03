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


class CustomUserAdminTests(TestCase):
    """The user admin must not let company staff escalate privileges or reach other companies."""

    def setUp(self):
        from django.contrib.auth.models import Group, Permission
        from django.urls import reverse

        from common.tests.factories import CompanyFactory, UserFactory

        self.reverse = reverse
        self.UserFactory = UserFactory
        self.company_a, self.company_b = CompanyFactory(), CompanyFactory()
        self.staff = UserFactory(company=self.company_a, is_staff=True, email='staff-a@example.com')
        self.staff.user_permissions.set(Permission.objects.filter(content_type__model='customuser', content_type__app_label='tenants'))
        self.colleague = UserFactory(company=self.company_a, email='colleague-a@example.com')
        self.outsider = UserFactory(company=self.company_b, email='outsider-b@example.com')
        self.root = UserFactory(company=self.company_a, is_superuser=True, is_staff=True, email='root-a@example.com')
        self.group = Group.objects.create(name='powerful')

        self.web = self.client_class()
        self.web.force_login(self.staff)

    # ---- visibility
    def test_changelist_shows_only_own_company_and_hides_superusers(self):
        body = self.web.get(self.reverse('admin:tenants_customuser_changelist')).content.decode()
        self.assertIn('colleague-a@example.com', body)
        self.assertNotIn('outsider-b@example.com', body)
        self.assertNotIn('root-a@example.com', body)

    def test_other_companys_and_superuser_records_are_unreachable(self):
        for user in (self.outsider, self.root):
            url = self.reverse('admin:tenants_customuser_change', args=[user.pk])
            self.assertNotContains(self.web.get(url, follow=True), user.email)
            self.assertEqual(self.web.post(url, {'email': 'changed@example.com'}).status_code, 302)
            user.refresh_from_db()
            self.assertNotEqual(user.email, 'changed@example.com')

    def test_cannot_change_password_of_other_company_user(self):
        url = self.reverse('admin:auth_user_password_change', args=[self.outsider.pk])
        self.web.post(url, {'password1': 'brand-new-pw-123', 'password2': 'brand-new-pw-123'})
        self.outsider.refresh_from_db()
        self.assertFalse(self.outsider.check_password('brand-new-pw-123'))

    def test_company_filter_is_not_offered_to_company_staff(self):
        body = self.web.get(self.reverse('admin:tenants_customuser_changelist')).content.decode()
        self.assertNotIn(self.company_b.name, body)

    # ---- no escalation
    def test_form_hides_privileged_fields(self):
        add = self.web.get(self.reverse('admin:tenants_customuser_add')).content.decode()
        change = self.web.get(self.reverse('admin:tenants_customuser_change', args=[self.colleague.pk])).content.decode()
        for html in (add, change):
            for field in ('company', 'is_superuser', 'groups', 'user_permissions'):
                self.assertNotIn(f'name="{field}"', html, field)

    def test_posted_privileged_fields_are_ignored_on_create(self):
        response = self.web.post(self.reverse('admin:tenants_customuser_add'), {
            'email': 'new@example.com', 'password1': 'a-long-passw0rd!', 'password2': 'a-long-passw0rd!',
            'is_staff': 'on', 'is_superuser': 'on', 'company': self.company_b.pk, 'groups': self.group.pk,
        })
        self.assertEqual(response.status_code, 302, getattr(response, 'content', b'')[:400])
        created = get_user_model().objects.get(email='new@example.com')
        self.assertEqual(created.company, self.company_a)       # stamped from the creator, not the payload
        self.assertFalse(created.is_superuser)
        self.assertFalse(created.groups.exists())
        self.assertTrue(created.is_staff)                        # allowed: it carries no permissions
        self.assertTrue(created.check_password('a-long-passw0rd!'))
        self.assertFalse(created.has_perm('inventory.view_product'))

    def test_posted_privileged_fields_are_ignored_on_update(self):
        url = self.reverse('admin:tenants_customuser_change', args=[self.colleague.pk])
        self.web.post(url, {
            'email': self.colleague.email, 'is_active': 'on', 'is_superuser': 'on',
            'company': self.company_b.pk, 'groups': self.group.pk, 'user_permissions': [],
        })
        self.colleague.refresh_from_db()
        self.assertFalse(self.colleague.is_superuser)
        self.assertEqual(self.colleague.company, self.company_a)
        self.assertFalse(self.colleague.groups.exists())

    def test_company_staff_can_deactivate_and_reset_password_of_a_colleague(self):
        url = self.reverse('admin:tenants_customuser_change', args=[self.colleague.pk])
        self.web.post(url, {'email': self.colleague.email})  # is_active omitted -> unchecked
        self.colleague.refresh_from_db()
        self.assertFalse(self.colleague.is_active)

        pw_url = self.reverse('admin:auth_user_password_change', args=[self.colleague.pk])
        response = self.web.post(pw_url, {'password1': 'brand-new-pw-123', 'password2': 'brand-new-pw-123'})
        self.assertEqual(response.status_code, 302)
        self.colleague.refresh_from_db()
        self.assertTrue(self.colleague.check_password('brand-new-pw-123'))

    def test_staff_without_permission_gets_nothing(self):
        self.staff.user_permissions.clear()
        self.assertEqual(self.web.get(self.reverse('admin:tenants_customuser_changelist')).status_code, 403)

    # ---- superusers
    def test_superuser_sees_and_sets_everything(self):
        web = self.client_class()
        web.force_login(self.root)
        body = web.get(self.reverse('admin:tenants_customuser_changelist')).content.decode()
        for email in ('colleague-a@example.com', 'outsider-b@example.com', 'root-a@example.com'):
            self.assertIn(email, body)

        change = web.get(self.reverse('admin:tenants_customuser_change', args=[self.outsider.pk])).content.decode()
        for field in ('company', 'is_superuser', 'groups', 'user_permissions'):
            self.assertIn(f'name="{field}"', change, field)

        response = web.post(self.reverse('admin:tenants_customuser_add'), {
            'email': 'for-b@example.com', 'password1': 'a-long-passw0rd!', 'password2': 'a-long-passw0rd!',
            'company': self.company_b.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_user_model().objects.get(email='for-b@example.com').company, self.company_b)
