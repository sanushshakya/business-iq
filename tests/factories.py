# tests/factories.py

import factory
from django.contrib.auth.models import User
from django.utils import timezone
from .models import Till, StockMovement

class UserFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating fake User instances.
    """

    class Meta:
        model = User

    username = factory.Sequence(lambda n: f'user{n}')
    password = factory.PostGenerationMethodCall('set_password', 'password')

class CompanyFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating fake Company instances.
    """

    class Meta:
        model = Company

    name = factory.Sequence(lambda n: f'Company {n}')

class OwnerFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating fake Owner instances.
    """

    class Meta:
        model = Owner

    user = factory.SubFactory(UserFactory)
    company = factory.SubFactory(CompanyFactory)

class ShopifyConnectionFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating fake ShopifyConnection instances.
    """

    class Meta:
        model = ShopifyConnection

    company = factory.SubFactory(OwnerFactory.company)
    shop_domain = factory.Sequence(lambda n: f'shop{n}.myshopify.com')
    access_token = factory.LazyAttribute(lambda _: get_random_string(length=32))

class TillFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating fake Till instances.
    """

    class Meta:
        model = Till

    company = factory.SubFactory(OwnerFactory.company)
    location = factory.Sequence(lambda n: f'Location {n}')

class StockMovementFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating fake StockMovement instances.
    """

    class Meta:
        model = StockMovement

    till = factory.SubFactory(TillFactory)
    product_name = factory.Sequence(lambda n: f'Product {n}')
    quantity = factory.LazyAttribute(lambda _: random.randint(1, 100))
    movement_type = 'IN'
    timestamp = factory.LazyFunction(timezone.now)