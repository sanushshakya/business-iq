# sales/tests/test_sale_transaction.py

"""A sale is one transaction of its own: if anything fails part-way, nothing of it stays (G26 review fix)."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.models import Product
from sales.models import Sale
from sales.tests.test_sales import make_till
from vat.models import VatRate


class SaleTransactionTests(TransactionTestCase):
    def setUp(self):
        VatRate.objects.get_or_create(code='zero', valid_from='1973-04-01', defaults={'rate': Decimal('0.00')})  # flushed
        company = CompanyFactory()
        cashier = UserFactory(company=company)
        till = make_till(company)
        rice = ProductFactory(company=company, name='Fake Rice', price=Decimal('2.50'), stock_quantity=0)
        StockBatchFactory(product=rice, quantity=5, expiration_date=timezone.localdate() + timedelta(days=9))
        Product.objects.filter(pk=rice.pk).update(stock_quantity=5)
        self.rice = rice
        self.api = APIClient()
        self.api.force_authenticate(cashier)
        self.session = self.api.post(reverse('tillsession-list'), {'till': till.pk, 'opening_float': '10.00'}).json()['id']

    def sell(self):
        return self.api.post(reverse('sale-list'), {'session': self.session, 'payment_method': 'card',
                                                    'items': [{'product': self.rice.pk, 'quantity': 2}]}, format='json')

    def stock(self):
        return Product.objects.get(pk=self.rice.pk).stock_quantity

    def test_a_failure_part_way_leaves_nothing(self):
        # Stock has already been taken (FEFO) when the allocation row fails: all of it must roll back.
        with mock.patch('sales.services.SaleAllocation.objects.create', side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):
                self.sell()
        self.assertEqual((Sale.objects.count(), self.stock()), (0, 5))

    def test_a_sale_commits(self):
        response = self.sell()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((Sale.objects.count(), self.stock()), (1, 3))
