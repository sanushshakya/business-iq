"""G58: promotions on till sales — discounts allocated to lines before VAT, the receipt's savings, the catalogue."""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse

from inventory.models import ProductCategory, StockBatch
from promotions.models import Promotion, PromotionGroup
from sales.models import SalePromotion
from sales.tests.test_sales import SalesTestCase
from sales.tests import test_weighed_sales
from tenants.models import Branch
from vat.services import uk_date

D = Decimal


class PromotionSaleTests(SalesTestCase):
    # Helpers only: inheriting (or importing the class here) would collect and run those tests again.
    weighed = test_weighed_sales.WeighedSaleTests.weighed
    charge = test_weighed_sales.WeighedSaleTests.charge

    def setUp(self):
        super().setUp()
        self.today = uk_date()
        self.wrap = self.stocked('2.50', name='Fake Wrap', vat_code='zero')
        self.crisps = self.stocked('1.00', name='Fake Crisps', vat_code='standard')
        self.cola = self.stocked('1.20', name='Fake Cola', vat_code='standard')

    def promotion(self, kind, name, products=(), groups=(), categories=(), branches=(), **fields):
        fields.setdefault('starts_on', self.today - timedelta(days=1))
        promotion = Promotion.objects.create(company=self.company, name=name, kind=kind, **fields)
        promotion.products.set(products)
        promotion.categories.set(categories)
        promotion.branches.set(branches)
        for position, members in enumerate(groups):
            group = PromotionGroup.objects.create(promotion=promotion, name=f'Group {position}', position=position)
            group.products.set(members)
        return promotion

    def meal_deal(self, **fields):
        return self.promotion('meal_deal', 'Fake Meal Deal', deal_price=D('3.50'),
                              groups=[[self.wrap], [self.crisps], [self.cola]], **fields)

    def test_meal_deal_over_mixed_vat_rates(self):
        deal = self.meal_deal()
        sale = self.sell(self.open()['id'], [(self.wrap, 1), (self.crisps, 1), (self.cola, 1)],
                         tendered='5.00').json()
        lines = {item['product_name']: item for item in sale['items']}
        # 4.70 for 3.50: 1.20 off by price — 0.64, 0.25, 0.31 — and VAT taken from what is left.
        self.assertEqual([(lines[n]['discount'], lines[n]['line_total'], lines[n]['net_amount'],
                           lines[n]['vat_amount']) for n in ('Fake Wrap', 'Fake Crisps', 'Fake Cola')],
                         [('0.64', '1.86', '1.86', '0.00'), ('0.25', '0.75', '0.62', '0.13'),
                          ('0.31', '0.89', '0.74', '0.15')])
        self.assertEqual((sale['total'], sale['change_due'], sale['total_saving']), ('3.50', '1.50', '1.20'))
        self.assertEqual([(r['vat_code'], r['net'], r['vat'], r['gross']) for r in sale['vat_summary']],
                         [('zero', '1.86', '0.00', '1.86'), ('standard', '1.36', '0.28', '1.64')])
        self.assertEqual(sale['promotions'], [{'promotion': deal.pk, 'name': 'Fake Meal Deal', 'kind': 'meal_deal',
                                               'times': 1, 'saving': '1.20'}])
        self.assertEqual(lines['Fake Wrap']['unit_price'], '2.50')  # the shelf price; the line shows the discount

    def test_multi_buy_cash_tendered_against_the_discounted_total(self):
        self.promotion('multi_buy', 'Fake 3 for £3', products=[self.cola], buy_quantity=3, deal_price=D('3.00'))
        session = self.open()['id']
        self.assertEqual(self.sell(session, [(self.cola, 3)], tendered='2.99').status_code, 400)
        sale = self.sell(session, [(self.cola, 3)], tendered='3.00').json()
        self.assertEqual((sale['total'], sale['change_due'], sale['items'][0]['discount']), ('3.00', '0.00', '0.60'))

    def test_bogof_and_percent_on_a_weighed_line(self):
        meat = ProductCategory.objects.create(name=f'Fake Meat {self.company.pk}')
        mince = self.weighed(per_kg='8.0000', batches=[(0, '5.000', 5)], category=meat)
        self.promotion('bogof', 'Fake BOGOF', products=[self.crisps], buy_quantity=1, get_quantity=1)
        self.promotion('category_percent', 'Fake 10% off meat', categories=[meat], percent=D('10.00'))
        sale = self.charge(self.open()['id'], [{'product': self.crisps.pk, 'quantity': 2},
                                               {'product': mince.pk, 'weight_kg': '0.500'}]).json()
        lines = {item['product_name']: item for item in sale['items']}
        self.assertEqual((lines['Fake Crisps']['line_total'], lines[mince.name]['line_total']), ('1.00', '3.60'))
        self.assertEqual(lines[mince.name]['weight_kg'], '0.500')
        self.assertEqual(sorted(p['name'] for p in sale['promotions']), ['Fake 10% off meat', 'Fake BOGOF'])
        self.assertEqual(sale['total'], '4.60')

    def test_only_promotions_in_force_here_today(self):
        elsewhere = Branch.objects.create(company=self.company, name='Fake Elsewhere', address='-')
        self.meal_deal(branches=[elsewhere])
        self.meal_deal(starts_on=self.today + timedelta(days=1))
        self.meal_deal(is_active=False)
        sale = self.sell(self.open()['id'], [(self.wrap, 1), (self.crisps, 1), (self.cola, 1)],
                         tendered='5.00').json()
        self.assertEqual((sale['total'], sale['promotions'], sale['total_saving']), ('4.70', [], '0.00'))
        self.meal_deal(branches=[self.till.branch])
        sale = self.sell(sale['session'],
                         [(self.wrap, 1), (self.crisps, 1), (self.cola, 1)], tendered='5.00').json()
        self.assertEqual(sale['total'], '3.50')

    def test_void_puts_stock_back_and_keeps_the_record(self):
        self.meal_deal()
        sale = self.sell(self.open()['id'], [(self.wrap, 1), (self.crisps, 1), (self.cola, 1)], tendered='5.00')
        sale = sale.json()
        voided = self.as_user(self.manager).post(reverse('sale-void', args=[sale['id']]), {'reason': 'Fake'})
        self.assertEqual(voided.status_code, 200, voided.content)
        self.assertEqual(sum(b.quantity for b in StockBatch.objects.filter(product=self.wrap)), 10)
        self.assertEqual(SalePromotion.objects.get(sale_id=sale['id']).saving, D('1.20'))

    def test_promotion_record_survives_the_promotion(self):
        deal = self.meal_deal()
        sale = self.sell(self.open()['id'], [(self.wrap, 1), (self.crisps, 1), (self.cola, 1)], tendered='5.00')
        Promotion.objects.filter(pk=deal.pk).update(name='Renamed')
        record = SalePromotion.objects.get(sale_id=sale.json()['id'])
        self.assertEqual((record.name, record.promotion_id), ('Fake Meal Deal', deal.pk))

    def test_catalogue_carries_promotions(self):
        live = self.meal_deal()
        soon = self.promotion('multi_buy', 'Fake Soon', products=[self.cola], buy_quantity=3, deal_price=D('3.00'),
                              **{'starts_on': self.today + timedelta(days=7)})
        self.promotion('multi_buy', 'Fake Later', products=[self.cola], buy_quantity=3, deal_price=D('3.00'),
                       **{'starts_on': self.today + timedelta(days=8)})
        self.promotion('multi_buy', 'Fake Over', products=[self.cola], buy_quantity=3, deal_price=D('3.00'),
                       ends_on=self.today - timedelta(days=1))
        self.promotion('multi_buy', 'Fake Off', products=[self.cola], buy_quantity=3, deal_price=D('3.00'),
                       is_active=False)
        catalogue = self.client.get(reverse('till-catalogue')).json()
        found = {p['name']: p for p in catalogue['promotions']}
        self.assertEqual(sorted(found), ['Fake Meal Deal', 'Fake Soon'])
        self.assertEqual(found['Fake Meal Deal']['groups'], [[self.wrap.pk], [self.crisps.pk], [self.cola.pk]])
        self.assertEqual((found['Fake Meal Deal']['id'], found['Fake Meal Deal']['deal_price'],
                          found['Fake Meal Deal']['branches']), (live.pk, '3.50', []))
        self.assertEqual(found['Fake Soon']['starts_on'], soon.starts_on.isoformat())
