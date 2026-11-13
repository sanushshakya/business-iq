"""G58: offline sales replayed with promotions — the till's discounted charges match the server's, VAT by rate."""

from datetime import timedelta
from decimal import Decimal

from promotions.models import Promotion, PromotionGroup
from sales.models import Sale, SaleItem, SalePromotion
from sales.tests import test_offline_sync
from vat.services import uk_date

D = Decimal


class OfflinePromotionTests(test_offline_sync.OfflineSyncTestCase):
    def setUp(self):
        super().setUp()
        self.wrap = self.stocked('2.50', name='Fake Wrap', vat_code='zero')
        self.crisps = self.stocked('1.00', name='Fake Crisps', vat_code='standard')
        self.cola = self.stocked('1.20', name='Fake Cola', vat_code='standard')

    def meal_deal(self, **fields):
        fields.setdefault('starts_on', uk_date() - timedelta(days=1))
        deal = Promotion.objects.create(company=self.company, name='Fake Meal Deal', kind='meal_deal',
                                        deal_price=D('3.50'), **fields)
        for position, product in enumerate((self.wrap, self.crisps, self.cola)):
            PromotionGroup.objects.create(promotion=deal, name=f'G{position}', position=position).products.add(product)
        return deal

    def basket(self, charged):
        return [{'product': p.pk, 'quantity': 1, 'charged': c} for p, c in zip((self.wrap, self.crisps, self.cola),
                                                                               charged)]

    def test_charged_with_the_deal_matches(self):
        deal = self.meal_deal()
        [result] = self.send(self.offline(self.basket(['1.86', '0.75', '0.89'])))
        self.assertEqual(result['status'], 'recorded')
        sale = Sale.objects.get()
        self.assertEqual((sale.total, self.kinds(sale.pk)), (D('3.50'), []))
        items = {i.product_name: i for i in SaleItem.objects.filter(sale=sale)}
        self.assertEqual([(items[n].discount, items[n].line_total, items[n].net_amount, items[n].vat_amount)
                          for n in ('Fake Wrap', 'Fake Crisps', 'Fake Cola')],
                         [(D('0.64'), D('1.86'), D('1.86'), D('0.00')), (D('0.25'), D('0.75'), D('0.62'), D('0.13')),
                          (D('0.31'), D('0.89'), D('0.74'), D('0.15'))])
        record = SalePromotion.objects.get(sale=sale)
        self.assertEqual((record.promotion_id, record.saving, record.times), (deal.pk, D('1.20'), 1))

    def test_charged_without_a_deal_the_server_knows(self):
        self.meal_deal()  # the till's catalogue did not have it yet
        [result] = self.send(self.offline(self.basket(['2.50', '1.00', '1.20'])))
        sale = Sale.objects.get(pk=result['sale'])
        self.assertEqual(self.kinds(sale.pk), ['price', 'price', 'price'])
        self.assertEqual(sale.total, D('4.70'))  # the customer paid that; it stands
        self.assertFalse(SalePromotion.objects.exists())
        self.assertEqual(sorted(SaleItem.objects.values_list('discount', flat=True)), [D('0')] * 3)
        crisps = SaleItem.objects.get(product=self.crisps)
        self.assertEqual((crisps.vat_amount, crisps.net_amount), (D('0.17'), D('0.83')))

    def test_charged_with_a_deal_since_ended(self):
        self.meal_deal(ends_on=uk_date() - timedelta(days=1))
        [result] = self.send(self.offline(self.basket(['1.86', '0.75', '0.89'])))
        self.assertEqual(self.kinds(result['sale']), ['price', 'price', 'price'])
        self.assertFalse(SalePromotion.objects.exists())

    def test_the_deal_on_the_sales_day_counts(self):
        # Sold yesterday evening (UK) while the deal ran; synced after it ended.
        self.meal_deal(starts_on=uk_date() - timedelta(days=5), ends_on=uk_date() - timedelta(days=1))
        [result] = self.send(self.offline(self.basket(['1.86', '0.75', '0.89']), minutes_ago=60 * 26))
        self.assertEqual(self.kinds(result['sale']), [])
        self.assertTrue(SalePromotion.objects.exists())

    def test_each_line_and_promotion_judged_on_its_own(self):
        crisps_deal = Promotion.objects.create(company=self.company, name='Fake 3 for £2.50', kind='multi_buy',
                                               buy_quantity=3, deal_price=D('2.50'),
                                               starts_on=uk_date() - timedelta(days=1))
        crisps_deal.products.add(self.crisps)
        bogof = Promotion.objects.create(company=self.company, name='Fake BOGOF', kind='bogof', buy_quantity=1,
                                         get_quantity=1, starts_on=uk_date() - timedelta(days=1))
        bogof.products.add(self.rice)  # the till's catalogue did not have this one yet
        [result] = self.send(self.offline([{'product': self.crisps.pk, 'quantity': 3, 'charged': '2.50'},
                                           self.rice_line(2)]))
        self.assertEqual(self.kinds(result['sale']), ['price'])  # the rice line only
        crisps = SaleItem.objects.get(product=self.crisps)
        self.assertEqual((crisps.discount, crisps.unit_price), (D('0.50'), D('1.00')))
        self.assertEqual(SaleItem.objects.get(product=self.rice).discount, D('0'))
        self.assertEqual(list(SalePromotion.objects.values_list('name', 'saving')), [('Fake 3 for £2.50', D('0.50'))])

    def test_a_label_that_no_longer_reads_is_still_a_label(self):
        from inventory.models import Product, ProductCategory

        drinks = ProductCategory.objects.create(name=f'Fake drinks {self.company.pk}')
        Product.objects.filter(pk=self.cola.pk).update(category=drinks)
        percent = Promotion.objects.create(company=self.company, name='Fake 10% off', kind='category_percent',
                                           percent=D('10.00'), starts_on=uk_date() - timedelta(days=1))
        percent.categories.add(drinks)
        # The till never discounts a scanned label; the server must not either, even when it can't read it now.
        [result] = self.send(self.offline([{'product': self.cola.pk, 'barcode': '2100000000001', 'charged': '1.20'}]))
        self.assertEqual(self.kinds(result['sale']), ['label'])
        self.assertFalse(SalePromotion.objects.exists())
