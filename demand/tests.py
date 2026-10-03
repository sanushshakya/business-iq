# demand/tests.py

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from common.tests.factories import UserFactory


class DemandApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(UserFactory())

    def test_create_demand(self):
        due = timezone.localdate() + timedelta(days=7)
        response = self.client.post(
            reverse('demand-list'), {'product_name': 'Dates', 'quantity': 20, 'due_date': due.isoformat()}
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['status'], 'PENDING')

    def test_rejects_negative_quantity_and_past_due_date(self):
        past = timezone.localdate() - timedelta(days=1)
        response = self.client.post(
            reverse('demand-list'), {'product_name': 'Dates', 'quantity': -1, 'due_date': past.isoformat()}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('quantity', response.json())
        self.assertIn('due_date', response.json())

    def test_cultural_event_dates_must_be_ordered(self):
        self.client.force_authenticate(UserFactory(is_staff=True))
        now = timezone.now()
        response = self.client.post(
            reverse('culturalevent-list'),
            {
                'name': 'Eid',
                'description': 'Festival',
                'start_date': now.isoformat(),
                'end_date': (now - timedelta(days=1)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 400)


class SeedCulturalEventsTests(TestCase):
    def test_is_idempotent(self):
        from io import StringIO

        from django.core.management import call_command

        from demand.models import CulturalEvent

        for _ in range(2):
            call_command('seed_cultural_events', stdout=StringIO())
        self.assertEqual(CulturalEvent.objects.count(), 3)
        self.assertTrue(all(e.end_date > e.start_date for e in CulturalEvent.objects.all()))
