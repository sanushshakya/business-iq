# sync/tests.py

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, UserFactory
from sync.models import ShopifyConnection, SyncTask
from sync.tasks import process_sync_task


class SyncTaskTests(TestCase):
    def test_process_sync_task_completes(self):
        SyncTask.objects.create(company=CompanyFactory(), task_id='t1', source_system='a', target_system='b')
        self.assertTrue(process_sync_task('t1'))
        task = SyncTask.objects.get(task_id='t1')
        self.assertEqual(task.status, 'completed')
        self.assertIsNotNone(task.start_time)
        self.assertIsNotNone(task.end_time)

    def test_unknown_task(self):
        self.assertFalse(process_sync_task('missing'))


class ShopifyConnectionApiTests(TestCase):
    def test_access_token_is_write_only(self):
        client = APIClient()
        client.force_authenticate(UserFactory(is_staff=True))
        company = CompanyFactory()
        response = client.post(
            reverse('shopifyconnection-list'),
            {'company': company.pk, 'shop_domain': 'x.myshopify.com', 'access_token': 'secret'},
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn('access_token', response.json())
        self.assertEqual(ShopifyConnection.objects.get().access_token, 'secret')


class EncryptedTokenTests(TestCase):
    def test_token_is_encrypted_in_the_database_but_readable_in_python(self):
        from django.db import connection

        conn = ShopifyConnection.objects.create(company=CompanyFactory(), shop_domain='e.myshopify.com', access_token='shpat_secret')
        with connection.cursor() as cursor:
            cursor.execute('SELECT access_token FROM sync_shopifyconnection WHERE id = %s', [conn.pk])
            raw = cursor.fetchone()[0]
        self.assertNotIn('shpat_secret', raw)
        self.assertEqual(ShopifyConnection.objects.get(pk=conn.pk).access_token, 'shpat_secret')

    def test_legacy_plaintext_rows_are_still_readable(self):
        from django.db import connection

        conn = ShopifyConnection.objects.create(company=CompanyFactory(), shop_domain='l.myshopify.com', access_token='x')
        with connection.cursor() as cursor:
            cursor.execute('UPDATE sync_shopifyconnection SET access_token = %s WHERE id = %s', ['plain-old-token', conn.pk])
        self.assertEqual(ShopifyConnection.objects.get(pk=conn.pk).access_token, 'plain-old-token')

    def test_key_rotation(self):
        from cryptography.fernet import Fernet
        from django.test import override_settings

        old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        with override_settings(FIELD_ENCRYPTION_KEYS=[old]):
            conn = ShopifyConnection.objects.create(company=CompanyFactory(), shop_domain='r.myshopify.com', access_token='tok')
        with override_settings(FIELD_ENCRYPTION_KEYS=[new, old]):  # new key first, old still accepted
            self.assertEqual(ShopifyConnection.objects.get(pk=conn.pk).access_token, 'tok')


class ShopDomainValidationTests(TestCase):
    """A tenant must not be able to aim the server (and its Shopify token) at an arbitrary host."""

    def setUp(self):
        self.client = APIClient()
        self.user = UserFactory(is_staff=True)
        self.client.force_authenticate(self.user)

    def create(self, domain):
        return self.client.post(
            reverse('shopifyconnection-list'),
            {'company': self.user.company.pk, 'shop_domain': domain, 'access_token': 'shpat_x'},
        )

    def test_real_shopify_domains_are_accepted(self):
        self.assertEqual(self.create('my-store.myshopify.com').status_code, 201)

    def test_anything_else_is_rejected(self):
        for bad in ('evil.com', 'localhost:8000', '169.254.169.254', 'my-store.myshopify.com.evil.com',
                    'evil.com/x.myshopify.com', 'http://my-store.myshopify.com', 'My-Store.myshopify.com'):
            response = self.create(bad)
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn('shop_domain', response.json(), bad)
        self.assertFalse(ShopifyConnection.objects.exists())

    def test_it_cannot_be_changed_to_a_bad_domain_later(self):
        pk = self.create('my-store.myshopify.com').json()['id']
        response = self.client.patch(reverse('shopifyconnection-detail', args=[pk]), {'shop_domain': 'evil.com'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ShopifyConnection.objects.get(pk=pk).shop_domain, 'my-store.myshopify.com')


class SyncTaskApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = UserFactory()
        self.client.force_authenticate(self.user)

    def test_system_fields_cannot_be_set_by_the_client(self):
        response = self.client.post(reverse('synctask-list'), {
            'source_system': 'a', 'target_system': 'b', 'task_id': 'mine', 'status': 'completed'})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body['status'], 'pending')
        self.assertNotEqual(body['task_id'], 'mine')
        self.assertTrue(body['task_id'])

    def test_tasks_cannot_be_edited(self):
        pk = self.client.post(reverse('synctask-list'), {'source_system': 'a', 'target_system': 'b'}).json()['id']
        response = self.client.patch(reverse('synctask-detail', args=[pk]), {'status': 'completed'})
        self.assertEqual(response.status_code, 405)

    def test_a_finished_task_is_not_run_again(self):
        SyncTask.objects.create(company=self.user.company, source_system='a', target_system='b', task_id='done',
                                status='completed')
        self.assertFalse(process_sync_task('done'))
        self.assertEqual(SyncTask.objects.get(task_id='done').status, 'completed')


class ShopifyConnectionRulesTests(TestCase):
    def setUp(self):
        self.staff = UserFactory(is_staff=True)
        self.member = UserFactory(company=self.staff.company)

    def payload(self, domain='a.myshopify.com'):
        return {'shop_domain': domain, 'access_token': 'shpat_x'}

    def test_only_staff_can_connect_a_store_but_members_can_see_it(self):
        as_member, as_staff = APIClient(), APIClient()
        as_member.force_authenticate(self.member)
        as_staff.force_authenticate(self.staff)
        self.assertEqual(as_member.post(reverse('shopifyconnection-list'), self.payload()).status_code, 403)
        self.assertEqual(as_staff.post(reverse('shopifyconnection-list'), self.payload()).status_code, 201)
        self.assertEqual(as_member.get(reverse('shopifyconnection-list')).json()['count'], 1)

    def test_a_company_has_one_store(self):
        client = APIClient()
        client.force_authenticate(self.staff)
        self.assertEqual(client.post(reverse('shopifyconnection-list'), self.payload()).status_code, 201)
        self.assertEqual(client.post(reverse('shopifyconnection-list'), self.payload('b.myshopify.com')).status_code, 400)

    def test_the_admin_hides_the_token_once_saved(self):
        from django.contrib import admin

        model_admin = admin.site._registry[ShopifyConnection]
        conn = ShopifyConnection.objects.create(company=self.staff.company, shop_domain='h.myshopify.com', access_token='t')
        self.assertEqual(model_admin.get_exclude(None, conn), ('access_token',))
        self.assertEqual(model_admin.get_exclude(None, None), ())
