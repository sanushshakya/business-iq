# common/tests/factories.py

from datetime import timedelta
from decimal import Decimal

import factory
from django.contrib.auth import get_user_model
from django.utils import timezone

from inventory.models import Product, StockBatch
from sync.models import ShopifyConnection
from tenants.models import Company


class CompanyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Company

    name = factory.Sequence(lambda n: f'Company {n}')
    registration_number = factory.Sequence(lambda n: f'REG{n:05d}')
    address = '1 Test Street'


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f'user{n}@example.com')
    company = factory.SubFactory(CompanyFactory)

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        self.set_password(extracted or 'password-12345')
        if create:
            self.save()


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Product

    name = factory.Sequence(lambda n: f'Product {n}')
    description = 'A product'
    company = factory.SubFactory(CompanyFactory)
    price = Decimal('100.00')
    stock_quantity = 50


def default_stock_location(company):
    """Where a test batch goes unless told: the first branch's shop floor, else a sellable MAIN (as the G09
    migration places existing stock)."""
    from inventory.ledger import primary_location

    return primary_location(company, create=True)


class StockBatchFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StockBatch

    product = factory.SubFactory(ProductFactory)
    batch_number = factory.Sequence(lambda n: f'B{n:05d}')
    quantity = 10
    expiration_date = factory.LazyFunction(lambda: timezone.localdate() + timedelta(days=30))
    location = factory.LazyAttribute(lambda o: default_stock_location(o.product.company))


class ShopifyConnectionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ShopifyConnection

    company = factory.SubFactory(CompanyFactory)
    shop_domain = factory.Sequence(lambda n: f'shop{n}.myshopify.com')
    access_token = 'test-token'


def history_movement(batch, quantity, movement_type='outbound'):
    """A past ledger row for tests (demand history): to CUSTOMER, or from SUPPLIER; does not change stock."""
    from inventory.ledger import virtual_location
    from inventory.models import Location, StockMovement

    company_id = batch.location.company_id
    if movement_type == 'outbound':
        source, destination = batch.location, virtual_location(company_id, Location.CUSTOMER)
    else:
        source, destination = virtual_location(company_id, Location.SUPPLIER), batch.location
    return StockMovement.objects.create(batch=batch, quantity=quantity, movement_type=movement_type,
                                        source_location=source, destination_location=destination)
