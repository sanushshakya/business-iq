# demand/management/commands/seed_cultural_events.py

"""
Seed placeholder cultural events (shared reference data). Safe to run more than once.

Product keywords are not seeded: an ``EventProductKeyword`` must point at a company's product,
so keywords are created through the API (``/demand/keywords/``).
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from demand.models import CulturalEvent

EVENTS = [
    ('Cultural Festival', 'Annual cultural festival celebrating diversity and traditions.', 30, 3),
    ('Art Exhibition', 'Exhibition showcasing local and international art pieces.', 60, 14),
    ('Science Workshop', 'Interactive workshop on various scientific topics for kids.', 90, 1),
]


class Command(BaseCommand):
    help = 'Seed cultural events into the database'

    def handle(self, *args, **options):
        now = timezone.now()
        created = 0
        for name, description, starts_in_days, length_days in EVENTS:
            start = now + timedelta(days=starts_in_days)
            _, was_created = CulturalEvent.objects.get_or_create(
                name=name,
                defaults={
                    'description': description,
                    'start_date': start,
                    'end_date': start + timedelta(days=length_days),
                },
            )
            created += was_created
        self.stdout.write(self.style.SUCCESS(f'Seeded cultural events ({created} new)'))
