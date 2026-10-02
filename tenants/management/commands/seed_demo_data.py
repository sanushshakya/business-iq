from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from tenants.models import Company


class Command(BaseCommand):
    """
    Management command to seed demo data for M18 Foods Ltd.

    Safe to run more than once: existing records are reused.
    """

    help = 'Seed demo data for M18 Foods Ltd'

    def add_arguments(self, parser):
        parser.add_argument('--owner-email', default='owner@m18foods.example')
        parser.add_argument('--owner-password', default='change-me-please')

    def handle(self, *args, **options):
        company, _ = Company.objects.get_or_create(
            registration_number='M18-DEMO',
            defaults={
                'name': 'M18 Foods Ltd',
                'address': '1234 Market St, San Francisco, CA 94105',
            },
        )

        User = get_user_model()
        if not User.objects.filter(email=options['owner_email']).exists():
            User.objects.create_user(
                email=options['owner_email'],
                password=options['owner_password'],
                company=company,
            )

        self.stdout.write(self.style.SUCCESS('Successfully created demo data for M18 Foods Ltd'))
