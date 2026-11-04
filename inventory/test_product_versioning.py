# inventory/test_product_versioning.py

"""G26: a product's version (``updated_at``) moves on every change a till must see, and deletions leave a marker."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory
from inventory.ledger import move, virtual_location
from inventory.models import Location, Product, ProductRemoval


class ProductVersionTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.product = ProductFactory(company=self.company, name='Fake Rice', price=Decimal('2.00'))
        self.long_ago = timezone.now() - timedelta(days=3)
        Product.objects.filter(pk=self.product.pk).update(updated_at=self.long_ago)

    def version(self):
        return Product.objects.get(pk=self.product.pk).updated_at

    def test_save_bumps_the_version(self):
        self.product.name = 'Fake Basmati'
        self.product.save()
        self.assertGreater(self.version(), self.long_ago)

    def test_saving_only_some_fields_bumps_the_version(self):  # price approvals and the storefront import do this
        self.product.price = Decimal('2.50')
        self.product.save(update_fields=['price'])
        self.assertGreater(self.version(), self.long_ago)

    def test_stock_movement_bumps_the_version(self):
        batch = StockBatchFactory(product=self.product, quantity=0)
        Product.objects.filter(pk=self.product.pk).update(updated_at=self.long_ago)
        with transaction.atomic():
            move(batch, 3, 'inbound', counterpart=virtual_location(self.company, Location.SUPPLIER))
        self.assertGreater(self.version(), self.long_ago)

    def test_deleting_leaves_a_removal_marker(self):
        pk = self.product.pk
        self.product.delete()
        removal = ProductRemoval.objects.get()
        self.assertEqual((removal.product_id, removal.company), (pk, self.company))

    def test_queryset_delete_leaves_markers(self):
        other = ProductFactory(company=self.company)
        Product.objects.filter(pk__in=[self.product.pk, other.pk]).delete()
        self.assertEqual(set(ProductRemoval.objects.values_list('product_id', flat=True)), {self.product.pk, other.pk})

    def test_deleting_the_company_leaves_no_markers(self):
        self.company.delete()
        self.assertFalse(ProductRemoval.objects.exists())
