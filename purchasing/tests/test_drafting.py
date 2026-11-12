"""G57: drafting purchase orders — normal week, an Eid peak, a supplier minimum; reruns, packs, preferred suppliers."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase

from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from demand_calendar.models import Event
from inventory.models import Product, ProductCategory, Supplier
from purchasing.drafting import draft_orders
from purchasing.models import PurchaseOrder, PurchaseOrderLine, SupplierProduct

D = Decimal
TODAY = date(2026, 3, 2)


class DraftCase(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.staff = UserFactory(company=self.company, is_staff=True)
        self.category = ProductCategory.objects.create(name=f'Fake dates {self.company.pk}')
        self.supplier = Supplier.objects.create(company=self.company, name='Fake Foods Ltd', contact_info='-',
                                                address='-', lead_time_days=3, order_cycle_days=7)
        self.rates = {}
        patcher = mock.patch('inventory.projections.average_daily_demand',
                             side_effect=lambda product, *a, **k: self.rates.get(product.pk, D('0')))
        patcher.start()
        self.addCleanup(patcher.stop)

    def product(self, name='Fake Medjool Dates', *, rate, stock, threshold=5, pack=12, cost='24.00',
                supplier=None, preferred=True, **fields):
        product = ProductFactory(company=self.company, name=name, stock_quantity=stock, reorder_threshold=threshold,
                                 category=self.category, **fields)
        self.rates[product.pk] = D(rate)
        SupplierProduct.objects.create(supplier=supplier or self.supplier, product=product, pack_size=pack,
                                       pack_cost=None if cost is None else D(cost), preferred=preferred)
        return product

    def draft(self, day=TODAY):
        return draft_orders(self.company, today=day, user=self.staff)

    def line(self, product):
        return PurchaseOrderLine.objects.get(product=product, order__status='draft')


class ScenarioTests(DraftCase):
    def test_normal_week(self):
        dates = self.product(rate='10', stock=40)  # horizon 3 + 7 days: 100 sold + 5 safety = 105; 65 short
        summary = self.draft()
        order = PurchaseOrder.objects.get()
        line = self.line(dates)
        self.assertEqual((line.packs, line.units, line.pack_size), (6, 72, 12))
        self.assertEqual((line.daily_demand, line.horizon_days, line.target, line.on_hand, line.on_order),
                         (D('10.00'), 10, 105, 40, 0))
        self.assertEqual(line.stockout_date, TODAY + timedelta(days=3))
        self.assertEqual((line.line_cost, order.total_cost), (D('144.00'), D('144.00')))
        self.assertEqual((order.number_display, order.status, order.auto, order.expected_on),
                         ('PO-000001', 'draft', True, TODAY + timedelta(days=3)))
        self.assertEqual(summary['created'], 1)

    def test_eid_peak(self):
        dates = self.product(rate='10', stock=40)
        eid = Event.objects.create(name='Fake Eid al-Fitr', start_date=TODAY + timedelta(days=4),
                                   end_date=TODAY + timedelta(days=6), demand_multiplier=D('3.00'))
        eid.product_categories.add(self.category)
        self.draft()
        line = self.line(dates)
        # 7 normal days (70) + 3 Eid days at ×3 (90) + 5 safety = 165; 125 short → 11 packs of 12.
        self.assertEqual((line.target, line.packs), (165, 11))
        self.assertIn('Fake Eid al-Fitr', line.events)

    def test_supplier_minimum_tops_up_the_soonest_to_run_out(self):
        Supplier.objects.filter(pk=self.supplier.pk).update(min_order_value=D('100.00'))
        soon = self.product('Fake Rice', rate='10', stock=20, pack=10, cost='10.00')  # 85 short: 9 packs, £90
        later = self.product('Fake Lentils', rate='1', stock=12, pack=10, cost='5.00')  # 3 short: 1 pack, £5
        self.draft()
        order = PurchaseOrder.objects.get()
        self.assertEqual((self.line(soon).packs, self.line(soon).topped_up), (10, 1))  # +1 pack: £100 + £5
        self.assertEqual(self.line(later).packs, 1)
        self.assertEqual((order.total_cost, order.below_minimum), (D('105.00'), False))

    def test_supplier_minimum_flagged_when_it_cannot_be_met_sensibly(self):
        Supplier.objects.filter(pk=self.supplier.pk).update(min_order_value=D('500.00'))
        rice = self.product('Fake Rice', rate='1', stock=10, pack=5, cost='10.00')  # target 15: 5 short, 1 pack
        self.draft()
        order = PurchaseOrder.objects.get()
        line = self.line(rice)
        self.assertLessEqual(line.on_hand + line.units, 2 * line.target)  # 10 held + 4 packs = 30 at most
        self.assertEqual((line.packs, order.total_cost, order.below_minimum), (4, D('40.00'), True))


class RuleTests(DraftCase):
    def test_nothing_needed_nothing_drafted(self):
        self.product(rate='1', stock=500)
        self.product('Fake Slow', rate='0', stock=50)  # no sales and above its threshold
        self.assertEqual(self.draft()['created'], 0)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_no_sales_history_but_below_threshold_orders_up_to_it(self):
        product = self.product(rate='0', stock=2, threshold=10, pack=6)
        self.draft()
        self.assertEqual((self.line(product).target, self.line(product).packs), (10, 2))

    def test_pack_rounding_and_no_cost(self):
        product = self.product(rate='0.1', stock=5, threshold=5, pack=24, cost=None)  # 1 short → 1 pack
        self.draft()
        line = self.line(product)
        self.assertEqual((line.packs, line.line_cost, line.order.total_cost), (1, D('0.00'), D('0.00')))

    def test_one_order_per_supplier_preferred_only(self):
        other = Supplier.objects.create(company=self.company, name='Fake Wholesale', contact_info='-', address='-')
        a = self.product('Fake A', rate='5', stock=0)
        b = self.product('Fake B', rate='5', stock=0, supplier=other)
        SupplierProduct.objects.create(supplier=other, product=a, pack_size=1)  # not preferred: ignored
        loner = ProductFactory(company=self.company, name='Fake Loner', stock_quantity=0)
        SupplierProduct.objects.create(supplier=other, product=loner, pack_size=1)  # no preferred supplier
        self.rates[loner.pk] = D('5')
        summary = self.draft()
        self.assertEqual(PurchaseOrder.objects.count(), 2)
        self.assertEqual(self.line(a).order.supplier, self.supplier)
        self.assertEqual(self.line(b).order.supplier, other)
        self.assertFalse(PurchaseOrderLine.objects.filter(product=loner).exists())
        self.assertEqual(summary['skipped'], 1)

    def test_loose_weighed_products_are_left_out(self):
        self.product('Fake Loose Mince', rate='5', stock=0, sold_by='weight')
        self.product('Fake Lamb Chops', rate='5', stock=0, sold_by='weight', is_catch_weight=True,
                     nominal_weight_kg=D('0.500'), price_per_kg=D('9.99'))
        self.draft()
        self.assertEqual(list(PurchaseOrderLine.objects.values_list('product__name', flat=True)), ['Fake Lamb Chops'])

    def test_a_rerun_rewrites_an_untouched_draft_keeping_its_number(self):
        product = self.product(rate='10', stock=40)
        self.draft()
        self.rates[product.pk] = D('20')
        summary = self.draft()
        order = PurchaseOrder.objects.get()
        self.assertEqual((summary['replaced'], order.number), (1, 1))
        self.assertEqual(self.line(product).target, 205)
        self.rates[product.pk] = D('0')
        Product.objects.filter(pk=product.pk).update(stock_quantity=500)
        self.draft()  # nothing needed now: cancelled, its number kept
        self.assertEqual(PurchaseOrder.objects.get().status, 'cancelled')
        self.rates[product.pk] = D('100')
        self.draft()
        self.assertEqual(PurchaseOrder.objects.get(status='draft').number, 2)

    def test_an_edited_draft_stands_for_its_supplier(self):
        dates = self.product(rate='10', stock=40)
        rice = self.product('Fake Rice', rate='10', stock=0)
        self.draft()
        order = PurchaseOrder.objects.get()
        PurchaseOrderLine.objects.filter(order=order, product=rice).delete()  # the buyer took rice out
        PurchaseOrder.objects.filter(pk=order.pk).update(edited=True, expected_on=TODAY - timedelta(days=5))
        summary = self.draft(TODAY + timedelta(days=4))
        self.assertEqual((summary['kept'], PurchaseOrder.objects.count()), (1, 1))  # rice does not come back
        self.assertEqual(list(order.lines.values_list('product', flat=True)), [dates.pk])

    def test_approved_orders_count_until_due_and_cancelled_ones_do_not(self):
        product = self.product(rate='10', stock=40)
        self.draft()
        first = PurchaseOrder.objects.get()
        PurchaseOrder.objects.filter(pk=first.pk).update(status='approved', expected_on=TODAY + timedelta(days=3))
        self.draft()
        self.assertFalse(PurchaseOrder.objects.filter(status='draft').exists())  # 72 on order covers it
        PurchaseOrder.objects.filter(pk=first.pk).update(expected_on=TODAY - timedelta(days=1))
        self.draft()  # due date passed: no longer on order
        self.assertEqual(self.line(product).on_order, 0)
        PurchaseOrder.objects.filter(pk=first.pk).update(status='cancelled', expected_on=TODAY + timedelta(days=3))
        self.draft()
        self.assertEqual(self.line(product).on_order, 0)

    def test_top_up_counts_what_is_held_and_on_order(self):
        Supplier.objects.filter(pk=self.supplier.pk).update(min_order_value=D('500.00'))
        product = self.product(rate='10', stock=95, pack=10, cost='10.00')  # target 105: 10 short, 1 pack
        self.draft()
        line = self.line(product)
        self.assertLessEqual(line.on_hand + line.units, 2 * line.target)  # at most 210 held
        self.assertEqual((line.packs, line.order.below_minimum), (11, True))

    def test_cultural_events_raise_demand_too(self):
        from django.utils import timezone

        from demand.models import CulturalEvent, EventProductKeyword

        dates = self.product(rate='10', stock=40)
        start = timezone.make_aware(timezone.datetime.combine(TODAY + timedelta(days=4), timezone.datetime.min.time()))
        eid = CulturalEvent.objects.create(name='Fake Eid al-Adha', description='-', start_date=start,
                                           end_date=start + timedelta(days=2))
        EventProductKeyword.objects.create(cultural_event=eid, product=dates, keyword='dates')
        self.draft()
        line = self.line(dates)
        self.assertEqual(line.target, 70 + 45 + 5)  # 3 days at the default ×1.5
        self.assertIn('Fake Eid al-Adha', line.events)

    def test_drafting_never_moves_stock(self):
        from inventory.models import StockMovement

        product = self.product(rate='10', stock=40)
        before = StockMovement.objects.count()
        self.draft()
        self.assertEqual(StockMovement.objects.count(), before)
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 40)

    def test_constraints(self):
        product = self.product(rate='1', stock=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SupplierProduct.objects.create(supplier=self.supplier, product=product, pack_size=2)
        other = Supplier.objects.create(company=self.company, name='Fake Other', contact_info='-', address='-')
        with self.assertRaises(IntegrityError), transaction.atomic():
            SupplierProduct.objects.create(supplier=other, product=product, pack_size=1, preferred=True)

    def test_only_this_company(self):
        self.product(rate='10', stock=0)
        other = CompanyFactory()
        self.assertEqual(draft_orders(other, today=TODAY)['created'], 0)
        self.assertFalse(PurchaseOrder.objects.filter(company=other).exists())
