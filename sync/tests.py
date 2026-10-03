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
        client.force_authenticate(UserFactory())
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
