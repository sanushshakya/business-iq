# demand/management/commands/seed_cultural_events.py

"""
Django management command to seed cultural events into the database.
"""

from django.core.management.base import BaseCommand
from demand.models import CulturalEvent, EventProductKeyword

class Command(BaseCommand):
    """
    Management command to insert predefined cultural events into the database.
    """

    help = 'Seed cultural events into the database'

    def handle(self, *args, **kwargs):
        """
        Handle the logic for seeding cultural events.
        """
        # Define a list of cultural events with their keywords
        cultural_events_data = [
            {
                'name': 'Cultural Festival',
                'description': 'Annual cultural festival celebrating diversity and traditions.',
                'keywords': ['cultural', 'festival', 'diversity']
            },
            {
                'name': 'Art Exhibition',
                'description': 'Exhibition showcasing local and international art pieces.',
                'keywords': ['art', 'exhibition', 'local']
            },
            {
                'name': 'Science Workshop',
                'description': 'Interactive workshop on various scientific topics for kids.',
                'keywords': ['science', 'workshop', 'kids']
            }
        ]

        # Create cultural events and associate them with keywords
        for event_data in cultural_events_data:
            cultural_event = CulturalEvent.objects.create(
                name=event_data['name'],
                description=event_data['description']
            )

            for keyword in event_data['keywords']:
                EventProductKeyword.objects.get_or_create(
                    cultural_event=cultural_event,
                    keyword=keyword
                )

        self.stdout.write(self.style.SUCCESS('Successfully seeded cultural events'))