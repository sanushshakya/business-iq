# inventory/test_product_plu.py

"""G18: a product's PLU, the item number a scale prints on its labels (unique per company, no leading zeros)."""

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from inventory.models import Product


class ProductPluTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.api = APIClient()
        self.api.force_authenticate(UserFactory(company=self.company, is_staff=True))

    def create(self, **fields):
        body = {'name': 'Fake Mince', 'description': 'x', 'price': '0.00', 'sold_by': 'weight',
                'price_per_kg': '8.9900', **fields}
        return self.api.post(reverse('product-list'), body, format='json')

    def test_plu_saved_without_leading_zeros(self):
        response = self.create(plu='00077')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['plu'], '77')

    def test_plu_must_be_digits(self):
        for plu in ('7a', '123456789', '0000', '７７'):
            response = self.create(plu=plu)
            self.assertEqual(response.status_code, 400, plu)
            self.assertIn('plu', response.json(), plu)

    def test_plu_unique_per_company(self):
        self.assertEqual(self.create(plu='77').status_code, 201)
        response = self.create(name='Fake Lamb', plu='077')
        self.assertEqual(response.status_code, 400)
        self.assertIn('plu', response.json())
        ProductFactory(company=CompanyFactory(), plu='77')  # another company may use it

    def test_blank_plu_for_many(self):
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create(name='Fake Lamb', plu='').status_code, 201)
        self.assertEqual(Product.objects.filter(plu='').count(), 2)

    def test_model_clean_normalises_and_refuses_a_duplicate(self):
        ProductFactory(company=self.company, plu='77')
        product = ProductFactory.build(company=self.company, plu='0077')
        with self.assertRaises(ValidationError) as caught:
            product.full_clean(exclude=['company'])  # as the admin form does
        self.assertIn('plu', caught.exception.message_dict)
        other = ProductFactory.build(company=self.company, plu='0042')
        other.full_clean(exclude=['company'])
        self.assertEqual(other.plu, '42')
