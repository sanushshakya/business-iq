# inventory/tests.py

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import ProductFactory, StockBatchFactory, UserFactory
from inventory.models import Product, ProductCategory


class InventoryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_requires_authentication(self):
        self.assertIn(self.client.get(reverse('product-list')).status_code, (401, 403))

    def test_product_crud(self):
        self.client.force_authenticate(UserFactory())
        category = ProductCategory.objects.create(name='Fruit')
        response = self.client.post(
            reverse('product-list'),
            {'name': 'Apple', 'description': 'Red', 'price': '1.50', 'stock_quantity': 10, 'category': category.pk},
        )
        self.assertEqual(response.status_code, 201, response.content)
        pk = response.json()['id']

        response = self.client.patch(reverse('product-detail', args=[pk]), {'price': '2.00'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(Product.objects.get(pk=pk).price), '2.00')

        self.assertEqual(self.client.delete(reverse('product-detail', args=[pk])).status_code, 204)

    def test_stock_batch_and_movement(self):
        user = UserFactory()
        self.client.force_authenticate(user)
        batch = StockBatchFactory(product__company=user.company)
        response = self.client.post(
            reverse('stockmovement-list'), {'batch': batch.pk, 'quantity': 3, 'movement_type': 'inbound'}
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.client.get(reverse('stockbatch-list')).status_code, 200)

    def test_str(self):
        self.assertEqual(str(ProductFactory(name='Pear')), 'Pear')


class BatchNumberUniquenessTests(TestCase):
    def setUp(self):
        self.user = UserFactory()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.product = ProductFactory(company=self.user.company)

    def payload(self, product, number='B-1'):
        return {'product': product.pk, 'batch_number': number, 'quantity': 5, 'expiration_date': '2027-01-01'}

    def test_other_companies_can_use_the_same_batch_number(self):
        self.assertEqual(self.client.post(reverse('stockbatch-list'), self.payload(self.product)).status_code, 201)

        other = UserFactory()
        other_client = APIClient()
        other_client.force_authenticate(other)
        theirs = ProductFactory(company=other.company)
        response = other_client.post(reverse('stockbatch-list'), self.payload(theirs))
        self.assertEqual(response.status_code, 201, response.content)

    def test_duplicates_within_a_product_are_a_clean_400(self):
        self.client.post(reverse('stockbatch-list'), self.payload(self.product))
        response = self.client.post(reverse('stockbatch-list'), self.payload(self.product))
        self.assertEqual(response.status_code, 400, response.content)

    def test_the_same_number_on_two_of_my_products_is_allowed(self):
        second = ProductFactory(company=self.user.company)
        self.assertEqual(self.client.post(reverse('stockbatch-list'), self.payload(self.product)).status_code, 201)
        self.assertEqual(self.client.post(reverse('stockbatch-list'), self.payload(second)).status_code, 201)
