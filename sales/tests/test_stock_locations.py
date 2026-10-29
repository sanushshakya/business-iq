# sales/tests/test_stock_locations.py

"""G09: the till sells from its branch's sellable locations only; a void puts units back where they came from."""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from common.tests.factories import ProductFactory, StockBatchFactory
from inventory.models import Location, StockBatch, StockMovement
from sales.tests.test_sales import SalesTestCase, make_till


class TillLocationTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.shop = Location.objects.get(branch=self.till.branch, type='shop_floor')
        self.product = ProductFactory(company=self.company, price=Decimal('2.00'), stock_quantity=0, name='Fake Rice')

    def batch(self, quantity, location, number, days=30):
        return StockBatchFactory(product=self.product, quantity=quantity, location=location, batch_number=number,
                                 expiration_date=timezone.localdate() + timedelta(days=days))

    def test_sale_takes_from_the_shop_floor_to_the_customer(self):
        batch = self.batch(5, self.shop, 'LOT-1')
        response = self.sell(self.open()['id'], [(self.product, 2)], method='card')
        self.assertEqual(response.status_code, 201, response.content)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.source_location, movement.destination_location.type), (self.shop, 'customer'))
        batch.refresh_from_db()
        self.assertEqual(batch.quantity, 3)

    def test_other_branchs_stock_is_not_sold(self):
        other_till = make_till(self.company)
        other_shop = Location.objects.get(branch=other_till.branch, type='shop_floor')
        other = self.batch(10, other_shop, 'LOT-OTHER')
        response = self.sell(self.open()['id'], [(self.product, 1)], method='card')
        self.assertEqual(response.status_code, 400)
        self.assertIn('only 0 in date', str(response.json()))
        other.refresh_from_db()
        self.assertEqual(other.quantity, 10)

    def test_non_sellable_location_in_the_branch_is_not_sold_from(self):
        back = Location.objects.create(company=self.company, branch=self.till.branch, code='BACK-1',
                                       name='Fake back store', type='back_store')
        self.batch(10, back, 'LOT-BACK', days=2)  # earlier expiry, but not sellable
        shop_batch = self.batch(4, self.shop, 'LOT-SHOP', days=20)
        response = self.sell(self.open()['id'], [(self.product, 3)], method='card')
        self.assertEqual(response.status_code, 201, response.content)
        shop_batch.refresh_from_db()
        self.assertEqual(shop_batch.quantity, 1)
        self.assertEqual(StockBatch.objects.get(batch_number='LOT-BACK').quantity, 10)

    def test_fefo_across_the_branchs_sellable_locations(self):
        butchery = Location.objects.create(company=self.company, branch=self.till.branch, code='BUTCH-1',
                                           name='Fake butchery', type='butchery', sellable=True)
        early = self.batch(2, butchery, 'LOT-EARLY', days=3)
        late = self.batch(5, self.shop, 'LOT-LATE', days=10)
        self.sell(self.open()['id'], [(self.product, 3)], method='card')
        early.refresh_from_db()
        late.refresh_from_db()
        self.assertEqual((early.quantity, late.quantity), (0, 4))

    def test_void_returns_units_to_the_original_batch_and_location(self):
        batch = self.batch(5, self.shop, 'LOT-1')
        sale = self.sell(self.open()['id'], [(self.product, 2)], method='card').json()
        response = self.as_user(self.manager).post(reverse('sale-void', args=[sale['id']]), {})
        self.assertEqual(response.status_code, 200, response.content)
        back = StockMovement.objects.filter(movement_type='inbound').get()
        self.assertEqual((back.source_location.type, back.destination_location, back.batch_id),
                         ('customer', self.shop, batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.quantity, 5)
