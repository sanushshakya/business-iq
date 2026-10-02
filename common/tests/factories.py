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
    price = Decimal('100.00')
    stock_quantity = 50


class StockBatchFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StockBatch

    product = factory.SubFactory(ProductFactory)
    batch_number = factory.Sequence(lambda n: f'B{n:05d}')
    quantity = 10
    expiration_date = factory.LazyFunction(lambda: timezone.localdate() + timedelta(days=30))


class ShopifyConnectionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ShopifyConnection

    company = factory.SubFactory(CompanyFactory)
    shop_domain = factory.Sequence(lambda n: f'shop{n}.myshopify.com')
    access_token = 'test-token'
