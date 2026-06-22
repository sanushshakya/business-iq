from django.core.management.base import BaseCommand
from authentication.models import Company, Owner
from common.models import ShopifyConnection

class Command(BaseCommand):
    """
    Management command to seed demo data for M18 Foods Ltd.
    """

    help = 'Seed demo data for M18 Foods Ltd'

    def handle(self, *args, **kwargs):
        # Create a sample company
        company = Company.objects.create(
            name='M18 Foods Ltd',
            address='1234 Market St, San Francisco, CA 94105'
        )

        # Create an owner for the company
        owner = Owner.objects.create(
            user=User.objects.create_user(username='owner', password=pbkdf2_hmac('sha256', b'password', get_random_string(32), 100000)),
            company=company,
            name='John Doe'
        )

        # Create a Shopify connection for the company
        shopify_connection = ShopifyConnection.objects.create(
            company=company,
            shop_domain='m18foods.myshopify.com',
            access_token='abc123def456ghi789jkl012'
        )

        self.stdout.write(self.style.SUCCESS('Successfully created demo data for M18 Foods Ltd'))