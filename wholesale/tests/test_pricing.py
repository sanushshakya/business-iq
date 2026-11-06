"""G39: resolving a trade customer's price — customer lists, then group lists, then the product's wholesale price.
All names here are fake."""

import time
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from common.tests.factories import CompanyFactory, ProductFactory
from inventory.models import Product
from wholesale.models import CustomerGroup, PriceList, PriceListEntry, TradeCustomer
from wholesale.pricing import BASE, CUSTOMER_LIST, GROUP_LIST, NoPrice, resolve_price, resolve_prices

D = Decimal
ON = date(2026, 11, 6)


class Scenario:
    """A company with one grouped customer, an each product (wholesale £5) and a weighed one (£9/kg)."""

    def __init__(self):
        self.company = CompanyFactory()
        self.group = CustomerGroup.objects.create(company=self.company, name='Fake Restaurants')
        self.customer = TradeCustomer.objects.create(company=self.company, account_code='C-1',
                                                     legal_name='E2E Fake Bistro Ltd', group=self.group)
        self.each = ProductFactory(company=self.company, wholesale_price=D('5.0000'))
        self.weighed = ProductFactory(company=self.company, sold_by=Product.WEIGHT, price_per_kg=D('12.99'),
                                      wholesale_price=D('9.0000'))

    def price_list(self, name, *, to=(), groups=(), entries=(), **fields):
        price_list = PriceList.objects.create(company=self.company, name=name, **fields)
        price_list.customers.set(to)
        price_list.groups.set(groups)
        for product, min_quantity, unit_price in entries:
            PriceListEntry.objects.create(price_list=price_list, product=product, min_quantity=D(min_quantity),
                                          unit_price=D(unit_price))
        return price_list


def _customer(s, **fields):
    return s.price_list('Customer', to=[s.customer], **fields)


def _group(s, **fields):
    return s.price_list('Group', groups=[s.group], **fields)


# Each case: (name, build(scenario), product attribute, quantity, expected (unit price, source, list name) or None).
CASES = [
    ('customer list wins over group and base',
     lambda s: (_customer(s, entries=[(s.each, '1', '4.10')]), _group(s, entries=[(s.each, '1', '4.50')])),
     'each', 1, ('4.1000', CUSTOMER_LIST, 'Customer')),
    ("group list when the customer's list doesn't price it",
     lambda s: (_customer(s, entries=[(s.weighed, '1', '8.00')]), _group(s, entries=[(s.each, '1', '4.50')])),
     'each', 1, ('4.5000', GROUP_LIST, 'Group')),
    ('base when no list prices it',
     lambda s: _customer(s, entries=[(s.weighed, '1', '8.00')]),
     'each', 3, ('5.0000', BASE, None)),
    ('no price: no list and no wholesale price',
     lambda s: Product.objects.filter(pk=s.each.pk).update(wholesale_price=None),
     'each', 1, None),
    ('the till price is never used',
     lambda s: Product.objects.filter(pk=s.weighed.pk).update(wholesale_price=None),
     'weighed', D('1.000'), None),
    ('a list not yet valid is skipped',
     lambda s: _customer(s, valid_from=date(2026, 11, 7), entries=[(s.each, '1', '4.10')]),
     'each', 1, ('5.0000', BASE, None)),
    ('a list that has ended is skipped',
     lambda s: _customer(s, valid_to=date(2026, 11, 5), entries=[(s.each, '1', '4.10')]),
     'each', 1, ('5.0000', BASE, None)),
    ('validity is inclusive at both ends',
     lambda s: _customer(s, valid_from=ON, valid_to=ON, entries=[(s.each, '1', '4.10')]),
     'each', 1, ('4.1000', CUSTOMER_LIST, 'Customer')),
    ('an inactive list is skipped',
     lambda s: (_customer(s, is_active=False, entries=[(s.each, '1', '4.10')]),
                _group(s, entries=[(s.each, '1', '4.50')])),
     'each', 1, ('4.5000', GROUP_LIST, 'Group')),
    ('below every break falls to the next level',
     lambda s: (_customer(s, entries=[(s.each, '10', '3.90')]), _group(s, entries=[(s.each, '5', '4.40')])),
     'each', 4, ('5.0000', BASE, None)),
    ('the largest break reached prices the whole line',
     lambda s: _customer(s, entries=[(s.each, '1', '4.50'), (s.each, '10', '4.20'), (s.each, '50', '3.90')]),
     'each', 49, ('4.2000', CUSTOMER_LIST, 'Customer')),
    ('a quantity equal to a break takes it',
     lambda s: _customer(s, entries=[(s.each, '1', '4.50'), (s.each, '10', '4.20'), (s.each, '50', '3.90')]),
     'each', 50, ('3.9000', CUSTOMER_LIST, 'Customer')),
    ('the highest priority wins at a level',
     lambda s: (s.price_list('Low', to=[s.customer], priority=-5, entries=[(s.each, '1', '3.00')]),
                s.price_list('High', to=[s.customer], priority=10, entries=[(s.each, '1', '4.40')])),
     'each', 1, ('4.4000', CUSTOMER_LIST, 'High')),
    ('on a priority tie the newest list wins',
     lambda s: (s.price_list('Older', to=[s.customer], priority=3, entries=[(s.each, '1', '3.00')]),
                s.price_list('Newer', to=[s.customer], priority=3, entries=[(s.each, '1', '4.40')])),
     'each', 1, ('4.4000', CUSTOMER_LIST, 'Newer')),
    ('a higher-priority list whose breaks are not reached loses to one that prices it',
     lambda s: (s.price_list('Bulk', to=[s.customer], priority=10, entries=[(s.each, '100', '2.00')]),
                s.price_list('Normal', to=[s.customer], entries=[(s.each, '1', '4.40')])),
     'each', 5, ('4.4000', CUSTOMER_LIST, 'Normal')),
    ('weighed: kg just below a break',
     lambda s: _customer(s, entries=[(s.weighed, '0.5', '8.80'), (s.weighed, '1.000', '8.20')]),
     'weighed', D('0.999'), ('8.8000', CUSTOMER_LIST, 'Customer')),
    ('weighed: kg exactly on a break',
     lambda s: _customer(s, entries=[(s.weighed, '0.5', '8.80'), (s.weighed, '1.000', '8.20')]),
     'weighed', D('1.000'), ('8.2000', CUSTOMER_LIST, 'Customer')),
    ('weighed: below every kg break falls to base',
     lambda s: _customer(s, entries=[(s.weighed, '0.5', '8.80')]),
     'weighed', D('0.250'), ('9.0000', BASE, None)),
    ('a customer with no group falls to base',
     lambda s: (_group(s, entries=[(s.each, '1', '4.50')]),
                TradeCustomer.objects.filter(pk=s.customer.pk).update(group=None)),
     'each', 1, ('5.0000', BASE, None)),
    ('a group with no lists falls to base',
     lambda s: None,
     'each', 1, ('5.0000', BASE, None)),
    ("another customer's list does not apply",
     lambda s: s.price_list('Theirs', to=[TradeCustomer.objects.create(
         company=s.company, account_code='C-2', legal_name='E2E Fake Other Ltd')], entries=[(s.each, '1', '1.00')]),
     'each', 1, ('5.0000', BASE, None)),
    ('a draft list (nobody assigned) applies to nobody',
     lambda s: s.price_list('Draft', entries=[(s.each, '1', '1.00')]),
     'each', 1, ('5.0000', BASE, None)),
    ('a zero price is a price',
     lambda s: _customer(s, entries=[(s.each, '1', '0')]),
     'each', 1, ('0.0000', CUSTOMER_LIST, 'Customer')),
]


class ResolveTableTests(TestCase):
    def test_cases(self):
        for name, build, product_name, quantity, expected in CASES:
            with self.subTest(name):
                s = Scenario()
                build(s)
                customer = TradeCustomer.objects.get(pk=s.customer.pk)
                product = Product.objects.get(pk=getattr(s, product_name).pk)
                [result] = resolve_prices(customer, [(product, quantity)], on=ON)
                if expected is None:
                    self.assertIsNone(result)
                    with self.assertRaises(NoPrice):
                        resolve_price(customer, product, quantity, on=ON)
                    continue
                unit_price, source, list_name = expected
                self.assertEqual(str(result.unit_price), unit_price)
                self.assertEqual(result.source, source)
                self.assertEqual(result.price_list.name if result.price_list else None, list_name)
                if list_name is None:
                    self.assertIsNone(result.min_quantity)


class ResolveRulesTests(TestCase):
    def setUp(self):
        self.s = Scenario()

    def test_resolved_price_names_the_break(self):
        _customer(self.s, entries=[(self.s.each, '1', '4.50'), (self.s.each, '10', '4.20')])
        result = resolve_price(self.s.customer, self.s.each, 12, on=ON)
        self.assertEqual(result.min_quantity, D('10'))
        self.assertEqual(result.unit_price, D('4.2000'))

    def test_no_rounding(self):
        _customer(self.s, entries=[(self.s.each, '1', '4.1234')])
        self.assertEqual(str(resolve_price(self.s.customer, self.s.each, 1, on=ON).unit_price), '4.1234')

    def test_date_defaults_to_the_uk_date(self):
        from unittest import mock

        _customer(self.s, valid_from=date(2026, 11, 7), entries=[(self.s.each, '1', '4.10')])
        with mock.patch('wholesale.pricing.uk_date', return_value=date(2026, 11, 7)):
            self.assertEqual(resolve_price(self.s.customer, self.s.each, 1).source, CUSTOMER_LIST)
        with mock.patch('wholesale.pricing.uk_date', return_value=date(2026, 11, 6)):
            self.assertEqual(resolve_price(self.s.customer, self.s.each, 1).source, BASE)

    def test_another_companys_product_is_refused(self):
        foreign = ProductFactory(company=CompanyFactory(), wholesale_price=D('1.0000'))
        with self.assertRaises(ValueError):
            resolve_prices(self.s.customer, [(self.s.each, 1), (foreign, 1)], on=ON)

    def test_another_companys_list_never_applies(self):
        # Assigned through the M2M directly, as no API or admin form allows.
        other = PriceList.objects.create(company=CompanyFactory(), name='Theirs')
        other.customers.add(self.s.customer)
        PriceListEntry.objects.create(price_list=other, product=self.s.each, unit_price=D('1'))
        self.assertEqual(resolve_price(self.s.customer, self.s.each, 1, on=ON).source, BASE)

    def test_bad_quantities_are_refused(self):
        for quantity in (0, -1, D('0'), D('1.5'), 1.0, True, '2'):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                resolve_prices(self.s.customer, [(self.s.each, quantity)], on=ON)
        for kg in (D('0.0005'), D('1E+30'), D('NaN'), D('Infinity')):  # kg to 3 dp; huge or odd
            with self.subTest(kg=kg), self.assertRaises(ValueError):
                resolve_prices(self.s.customer, [(self.s.weighed, kg)], on=ON)
        self.assertEqual(len(resolve_prices(self.s.customer, [(self.s.weighed, D('0.001'))], on=ON)), 1)

    def test_the_same_product_on_several_lines(self):
        _customer(self.s, entries=[(self.s.each, '1', '4.50'), (self.s.each, '10', '4.20')])
        first, second = resolve_prices(self.s.customer, [(self.s.each, 2), (self.s.each, 20)], on=ON)
        self.assertEqual((first.unit_price, second.unit_price), (D('4.5000'), D('4.2000')))

    def test_no_lines(self):
        self.assertEqual(resolve_prices(self.s.customer, [], on=ON), [])


class ResolveSpeedTests(TestCase):
    """ROADMAP: a 200-line order resolves in under 200 ms, in a fixed number of queries."""

    def test_200_lines_under_200_ms_in_few_queries(self):
        s = Scenario()
        products = [ProductFactory(company=s.company, wholesale_price=D('2.0000')) for _ in range(200)]
        for n in range(10):
            s.price_list(f'Customer {n}', to=[s.customer], priority=n,
                         entries=[(p, q, '1.50') for p in products[n::10] for q in ('1', '10', '100')])
            s.price_list(f'Group {n}', groups=[s.group], priority=n,
                         entries=[(p, '1', '1.75') for p in products[n::5]])
        lines = [(p, 5 + i % 200) for i, p in enumerate(products)]

        def run():
            with CaptureQueriesContext(connection) as queries:
                started = time.perf_counter()
                results = resolve_prices(s.customer, lines, on=ON)
                elapsed = time.perf_counter() - started
            return results, elapsed, len(queries)

        results, elapsed, query_count = run()
        self.assertEqual(len(results), 200)
        self.assertTrue(all(r is not None for r in results))
        self.assertLess(elapsed, 0.2)
        self.assertLessEqual(query_count, 4)
        with CaptureQueriesContext(connection) as small:
            resolve_prices(s.customer, lines[:3], on=ON)
        self.assertEqual(len(small), query_count)  # the count does not grow with the lines
