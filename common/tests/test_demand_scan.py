# common/tests/test_demand_scan.py

"""``scan_demand_alerts``: stock-up alerts ahead of calendar and cultural events."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from common.models import DemandAlert, Setting
from common.services.settings_service import get_company_setting
from common.tasks import scan_demand_alerts
from demand.models import CulturalEvent, EventProductKeyword
from demand_calendar.models import Event
from inventory.models import ProductCategory, StockMovement

from .factories import CompanyFactory, ProductFactory, StockBatchFactory


class DemandScanTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.company = CompanyFactory()
        self.fruit = ProductCategory.objects.create(name='Dried fruit')
        self.drinks = ProductCategory.objects.create(name='Drinks')

    def product(self, company=None, category=None, stock=10, sold_last_30_days=60, **kwargs):
        product = ProductFactory(company=company or self.company, category=category or self.fruit,
                                 stock_quantity=stock, reorder_threshold=5, **kwargs)
        if sold_last_30_days:
            batch = StockBatchFactory(product=product)
            StockMovement.objects.create(batch=batch, quantity=sold_last_30_days, movement_type='outbound')
        return product

    def event(self, starts_in=10, length=4, multiplier='2.00', categories=None):
        event = Event.objects.create(
            name='Test event', start_date=self.today + timedelta(days=starts_in),
            end_date=self.today + timedelta(days=starts_in + length - 1), demand_multiplier=Decimal(multiplier))
        event.product_categories.set(categories if categories is not None else [self.fruit])
        return event

    # ---- calendar events
    def test_alert_covers_the_extra_demand_the_stock_cannot(self):
        product = self.product(stock=10)       # sells 60 / 30 days = 2 a day
        event = self.event()                    # 4 days at 2x -> 2 * 4 * 2 = 16 units, 10 in stock
        self.assertEqual(scan_demand_alerts(), 1)
        alert = DemandAlert.objects.get()
        self.assertEqual((alert.company, alert.product, alert.requested_qty, alert.event, alert.is_handled),
                         (self.company, product.name, 6, event, False))
        self.assertEqual(alert.branch, 'All branches')

    def test_no_alert_when_stock_already_covers_the_event(self):
        self.product(stock=100)
        self.event()
        self.assertEqual(scan_demand_alerts(), 0)

    def test_running_twice_does_not_duplicate_alerts(self):
        self.product(stock=10)
        self.event()
        self.assertEqual(scan_demand_alerts(), 1)
        self.assertEqual(scan_demand_alerts(), 0)
        self.assertEqual(DemandAlert.objects.count(), 1)

    def test_a_handled_alert_is_not_raised_again(self):
        self.product(stock=10)
        self.event()
        scan_demand_alerts()
        DemandAlert.objects.update(is_handled=True)
        self.assertEqual(scan_demand_alerts(), 0)
        self.assertEqual(DemandAlert.objects.filter(is_handled=False).count(), 0)

    def test_only_events_inside_the_lead_window_count(self):
        self.product(stock=10)
        self.event(starts_in=60)
        self.assertEqual(scan_demand_alerts(), 0)
        self.assertEqual(scan_demand_alerts(lead_days=90), 1)

    def test_past_events_are_ignored_but_a_running_event_counts(self):
        self.product(stock=10)
        self.event(starts_in=-20, length=4)
        self.assertEqual(scan_demand_alerts(), 0)
        self.event(starts_in=-1, length=4)
        self.assertEqual(scan_demand_alerts(), 1)

    def test_only_products_in_the_events_categories_are_alerted(self):
        self.product(stock=10)
        self.product(stock=10, category=self.drinks)
        self.event()
        scan_demand_alerts()
        self.assertEqual(DemandAlert.objects.count(), 1)

    def test_an_event_with_no_categories_alerts_nobody(self):
        self.product(stock=10)
        self.event(categories=[])
        self.assertEqual(scan_demand_alerts(), 0)

    def test_every_company_gets_its_own_alerts(self):
        rival = CompanyFactory()
        mine = self.product(stock=10)
        theirs = self.product(company=rival, stock=10)
        self.event()
        self.assertEqual(scan_demand_alerts(), 2)
        self.assertEqual(DemandAlert.objects.get(company=self.company).product, mine.name)
        self.assertEqual(DemandAlert.objects.get(company=rival).product, theirs.name)

    def test_without_sales_history_the_reorder_threshold_is_the_baseline(self):
        self.product(stock=3, sold_last_30_days=0)     # threshold 5 x 2.0 = 10 needed, 3 in stock
        self.event()
        scan_demand_alerts()
        self.assertEqual(DemandAlert.objects.get().requested_qty, 7)

    def test_old_sales_do_not_count(self):
        product = self.product(stock=10, sold_last_30_days=0)
        batch = StockBatchFactory(product=product)
        movement = StockMovement.objects.create(batch=batch, quantity=600, movement_type='outbound')
        StockMovement.objects.filter(pk=movement.pk).update(date_time=timezone.now() - timedelta(days=90))
        self.event()
        scan_demand_alerts()
        self.assertEqual(DemandAlert.objects.count(), 0)  # baseline falls back to the threshold: 5 x 2.0 = 10, stock is 10

    # ---- cultural events (products linked by keyword)
    def cultural(self, product, starts_in=5, length=2):
        start = timezone.now() + timedelta(days=starts_in)
        event = CulturalEvent.objects.create(name='Festival', description='d', start_date=start, end_date=start + timedelta(days=length - 1))
        EventProductKeyword.objects.create(cultural_event=event, product=product, keyword='festival')
        return event

    def test_linked_products_get_an_alert_with_the_default_multiplier(self):
        product = self.product(stock=2, sold_last_30_days=60)   # 2/day x 2 days x 1.5 = 6, stock 2 -> 4
        event = self.cultural(product)
        self.assertEqual(scan_demand_alerts(), 1)
        alert = DemandAlert.objects.get()
        self.assertEqual((alert.cultural_event, alert.event, alert.requested_qty), (event, None, 4))
        self.assertEqual(scan_demand_alerts(), 0)

    def test_the_multiplier_comes_from_the_companys_setting(self):
        Setting.objects.create(company=self.company, key='cultural_event_multiplier', value='3')
        product = self.product(stock=2, sold_last_30_days=60)   # 2 x 2 x 3 = 12 - 2 = 10
        self.cultural(product)
        scan_demand_alerts()
        self.assertEqual(DemandAlert.objects.get().requested_qty, 10)

    def test_a_bad_setting_falls_back_to_the_default(self):
        Setting.objects.create(company=self.company, key='cultural_event_multiplier', value='lots')
        product = self.product(stock=2, sold_last_30_days=60)
        self.cultural(product)
        scan_demand_alerts()
        self.assertEqual(DemandAlert.objects.get().requested_qty, 4)


class CompanySettingHelperTests(TestCase):
    def test_values_are_cast_and_default_when_missing_or_invalid(self):
        company = CompanyFactory()
        Setting.objects.create(company=company, key='margin', value=' 25 ')
        Setting.objects.create(company=company, key='bad', value='abc')
        self.assertEqual(get_company_setting(company.pk, 'margin', 0, int), 25)
        self.assertEqual(get_company_setting(company.pk, 'margin', 0, Decimal), Decimal('25'))
        self.assertEqual(get_company_setting(company.pk, 'bad', 7, int), 7)
        self.assertEqual(get_company_setting(company.pk, 'missing', 'x'), 'x')
        self.assertEqual(get_company_setting(CompanyFactory().pk, 'margin', 'other'), 'other')  # not another company's
