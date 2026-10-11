# demand/tests.py

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from common.tests.factories import ProductFactory, UserFactory
from demand.models import CulturalEvent, Demand


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

    def test_zero_quantity_is_rejected(self):
        due = timezone.localdate() + timedelta(days=7)
        response = self.client.post(
            reverse('demand-list'), {'product_name': 'Dates', 'quantity': 0, 'due_date': due.isoformat()}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('quantity', response.json())

    def test_demands_are_private_to_their_company(self):
        other = Demand.objects.create(
            company=UserFactory().company, product_name='Secret', quantity=1, due_date=timezone.localdate())
        self.assertEqual(self.client.get(reverse('demand-list')).json()['count'], 0)
        self.assertEqual(self.client.get(reverse('demand-detail', args=[other.pk])).status_code, 404)

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


class EventKeywordApiTests(TestCase):
    def setUp(self):
        self.user = UserFactory()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        now = timezone.now()
        self.event = CulturalEvent.objects.create(
            name='Eid', description='Festival', start_date=now, end_date=now + timedelta(days=3))

    def link(self, product, keyword='festival'):
        return self.client.post(
            reverse('eventproductkeyword-list'),
            {'cultural_event': self.event.pk, 'product': product.pk, 'keyword': keyword},
        )

    def test_a_product_is_linked_to_an_event_once(self):
        product = ProductFactory(company=self.user.company)
        self.assertEqual(self.link(product).status_code, 201)
        self.assertEqual(self.link(product, 'gift').status_code, 400)

    def test_blank_keyword_is_rejected(self):
        self.assertEqual(self.link(ProductFactory(company=self.user.company), '   ').status_code, 400)

    def test_cannot_link_another_companys_product(self):
        self.assertEqual(self.link(ProductFactory(company=UserFactory().company)).status_code, 400)
