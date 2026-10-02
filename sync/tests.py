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
