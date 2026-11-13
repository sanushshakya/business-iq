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
