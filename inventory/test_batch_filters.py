# inventory/test_batch_filters.py

"""G13: the batch list filters by location and product (the Inventory screen's stock per location)."""

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, StockBatchFactory, UserFactory
from inventory.models import Location
from tenants.models import Branch


class BatchFilterTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        branch = Branch.objects.create(company=self.company, name='Fake High Street', address='x')
        self.shop = Location.objects.get(branch=branch, type='shop_floor')
        self.wh = Location.objects.create(company=self.company, code='WH-1', name='Fake warehouse', type='warehouse')
        self.rice = ProductFactory(company=self.company, name='Fake rice')
        self.dates = ProductFactory(company=self.company, name='Fake dates')
        self.at_shop = StockBatchFactory(product=self.rice, quantity=3, location=self.shop, batch_number='LOT-1')
        self.at_wh = StockBatchFactory(product=self.rice, quantity=7, location=self.wh, batch_number='LOT-1')
        self.dates_wh = StockBatchFactory(product=self.dates, quantity=2, location=self.wh, batch_number='LOT-2')
        self.api = APIClient()
        self.api.force_authenticate(UserFactory(company=self.company, is_staff=True))

    def ids(self, **params):
        response = self.api.get(reverse('stockbatch-list'), params)
        self.assertEqual(response.status_code, 200)
        return {row['id'] for row in response.json()['results']}

    def test_batches_filter_by_location_and_product(self):
        self.assertEqual(self.ids(location=self.wh.pk), {self.at_wh.pk, self.dates_wh.pk})
        self.assertEqual(self.ids(product=self.rice.pk), {self.at_shop.pk, self.at_wh.pk})
        self.assertEqual(self.ids(location=self.wh.pk, product=self.rice.pk), {self.at_wh.pk})
        self.assertEqual(len(self.ids(location='x')), 3)  # not a number: ignored

    def test_other_company_location_filter_returns_nothing(self):
        other = CompanyFactory()
        theirs = Location.objects.create(company=other, code='WH-1', name='Fake other', type='warehouse')
        StockBatchFactory(product=ProductFactory(company=other), quantity=5, location=theirs)
        self.assertEqual(self.ids(location=theirs.pk), set())
