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
        self.assertEqual(events[0]['id'], soon.pk)
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


class SyncIslamicEventsTests(TestCase):
    def events(self, *pairs):
        from common.services.hijri_calendar_service import HijriEvent

        return [HijriEvent(name=name, date=when) for name, when in pairs]

    def run_sync(self, events):
        from unittest import mock

        from demand_calendar.tasks import sync_islamic_events

        with mock.patch('demand_calendar.tasks.HijriCalendarService') as service:
            service.return_value.get_upcoming_events.return_value = events
            return sync_islamic_events()

    def test_creates_events_with_sensible_lengths_and_multipliers(self):
        from datetime import date
        from decimal import Decimal

        created = self.run_sync(self.events(('Start of Ramadan', date(2027, 2, 8)), ('Eid al-Fitr', date(2027, 3, 9)), ('Ashura', date(2027, 7, 15))))
        self.assertEqual(created, 3)
        ramadan, eid, ashura = (Event.objects.get(name=n) for n in ('Start of Ramadan', 'Eid al-Fitr', 'Ashura'))
        self.assertEqual((ramadan.end_date, ramadan.demand_multiplier), (date(2027, 3, 9), Decimal('1.50')))
        self.assertEqual((eid.end_date, eid.demand_multiplier), (date(2027, 3, 11), Decimal('1.80')))
        self.assertEqual((ashura.end_date, ashura.demand_multiplier), (date(2027, 7, 15), Decimal('1.00')))

    def test_is_idempotent_and_keeps_edits_made_by_staff(self):
        from datetime import date
        from decimal import Decimal

        from inventory.models import ProductCategory

        events = self.events(('Eid al-Adha', date(2027, 5, 16)))
        self.assertEqual(self.run_sync(events), 1)
        event = Event.objects.get()
        event.demand_multiplier = Decimal('2.50')
        event.save()
        event.product_categories.set([ProductCategory.objects.create(name='Meat')])

        self.assertEqual(self.run_sync(events), 0)
        event.refresh_from_db()
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(event.demand_multiplier, Decimal('2.50'))
        self.assertEqual(event.product_categories.count(), 1)

    def test_the_same_event_next_year_is_a_new_row(self):
        from datetime import date

        self.run_sync(self.events(('Eid al-Fitr', date(2027, 3, 9))))
        self.assertEqual(self.run_sync(self.events(('Eid al-Fitr', date(2028, 2, 26)))), 1)
        self.assertEqual(Event.objects.count(), 2)

    def test_an_unreachable_calendar_service_creates_nothing(self):
        self.assertEqual(self.run_sync([]), 0)
