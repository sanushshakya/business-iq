# demand_calendar/tests.py

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from common.tests.factories import UserFactory
from demand_calendar.models import DemandAlert, Event
from inventory.models import ProductCategory


class UpcomingEventsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(UserFactory())
        self.today = timezone.localdate()

    def make_event(self, name, start_offset, length=3):
        return Event.objects.create(
            name=name,
            start_date=self.today + timedelta(days=start_offset),
            end_date=self.today + timedelta(days=start_offset + length),
            demand_multiplier='1.50',
        )

    def test_only_events_in_window_are_returned(self):
        soon = self.make_event('Soon', 5)
        self.make_event('Far away', 200)
        self.make_event('Past', -30, length=2)
        category = ProductCategory.objects.create(name='Dates')
        soon.product_categories.add(category)
        DemandAlert.objects.create(event=soon, url='https://example.com/alert')

        response = self.client.get(reverse('get_next_three_months_events'))
        self.assertEqual(response.status_code, 200)
        events = [e for month in response.json() for e in month['events']]
        self.assertEqual([e['name'] for e in events], ['Soon'])
        self.assertEqual(events[0]['product_categories'], ['Dates'])
        self.assertEqual(events[0]['demand_alert_link'], 'https://example.com/alert')

    def test_event_without_alert_has_no_link(self):
        self.make_event('Plain', 1)
        events = [e for m in self.client.get(reverse('get_next_three_months_events')).json() for e in m['events']]
        self.assertIsNone(events[0]['demand_alert_link'])

    def test_event_api_lists_alerts(self):
        event = self.make_event('E', 1)
        DemandAlert.objects.create(event=event, url='https://example.com/a')
        data = self.client.get(reverse('event-detail', args=[event.pk])).json()
        self.assertEqual(len(data['demand_alerts']), 1)
