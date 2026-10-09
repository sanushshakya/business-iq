# common/tests/test_integrations.py

"""
The Hijri calendar, UK tariff and Shopify integrations.

HTTP is mocked, but with the shapes the real services return: ``fixtures/tariff_0804100099.json`` is a
trimmed copy of a real Trade Tariff response, and the AlAdhan payloads mirror real ``gToH``/``hToG`` output.
``test_live_integrations.py`` checks the same code against the real services (opt in).
"""

import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from common.services.cost_calculation_service import CostCalculationService
from common.services.hijri_calendar_service import HijriCalendarService
from common.services.hmrctariff_service import HMRCTariffService, TariffDuty, TariffLookupError
from common.services.shopify_service import ShopifyError, ShopifyService

from .factories import ShopifyConnectionFactory

FIXTURES = Path(__file__).parent / 'fixtures'


def response(payload=None, status=200):
    """A stand-in for ``requests.Response``."""
    mocked = mock.Mock(status_code=status)
    mocked.json.return_value = payload
    mocked.raise_for_status.side_effect = requests.HTTPError(f'HTTP {status}') if status >= 400 else None
    return mocked


# ---------------------------------------------------------------------------------------------
# Hijri calendar (AlAdhan)
# ---------------------------------------------------------------------------------------------

def aladhan(hijri_today, conversions):
    """
    Fake ``requests.get`` for AlAdhan. ``hijri_today`` is (year, month, day); ``conversions`` maps
    "DD-MM-YYYY" (Hijri) to the Gregorian "DD-MM-YYYY" the API would answer.
    """
    def fake_get(url, **kwargs):
        path = url.split('/v1/')[1]
        kind, value = path.split('/')
        if kind == 'gToH':
            year, month, day = hijri_today
            return response({'code': 200, 'status': 'OK', 'data': {'hijri': {
                'date': f'{day:02d}-{month:02d}-{year}', 'day': str(day), 'year': str(year),
                'month': {'number': month, 'en': 'x'}}}})
        if kind == 'hToG':
            return response({'code': 200, 'status': 'OK', 'data': {'gregorian': {'date': conversions[value]}}})
        raise AssertionError(f'unexpected URL {url}')
    return fake_get


class HijriCalendarServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.service = HijriCalendarService()

    def next_event(self, hijri_today, conversions, today=date(2026, 10, 5)):
        with mock.patch('common.services.hijri_calendar_service.requests.get', side_effect=aladhan(hijri_today, conversions)) as get:
            return self.service.get_next_event(today), get

    def test_next_event_later_this_hijri_year(self):
        event, get = self.next_event((1448, 4, 24), {'01-09-1448': '08-02-2027'})
        self.assertEqual((event.name, event.date), ('Start of Ramadan', date(2027, 2, 8)))
        urls = [call.args[0] for call in get.call_args_list]
        self.assertEqual(urls, ['https://api.aladhan.com/v1/gToH/05-10-2026', 'https://api.aladhan.com/v1/hToG/01-09-1448'])

    def test_rolls_over_to_next_hijri_year(self):
        event, _ = self.next_event((1448, 12, 11), {'01-01-1449': '06-06-2027'}, today=date(2027, 5, 18))
        self.assertEqual((event.name, event.date), ('Islamic New Year', date(2027, 6, 6)))

    def test_an_event_today_counts(self):
        event, _ = self.next_event((1448, 10, 1), {'01-10-1448': '09-03-2027'}, today=date(2027, 3, 9))
        self.assertEqual((event.name, event.date), ('Eid al-Fitr', date(2027, 3, 9)))

    def test_skips_a_candidate_the_two_calendars_place_in_the_past(self):
        event, _ = self.next_event(
            (1448, 9, 1), {'01-09-1448': '07-02-2027', '27-09-1448': '06-03-2027'}, today=date(2027, 2, 8))
        self.assertEqual((event.name, event.date), ('Laylat al-Qadr', date(2027, 3, 6)))

    def test_date_helper_and_result_are_cached(self):
        with mock.patch('common.services.hijri_calendar_service.requests.get', side_effect=aladhan((1448, 4, 24), {'01-09-1448': '08-02-2027'})) as get:
            first = self.service.get_next_event_date(date(2026, 10, 5))
            second = self.service.get_next_event_date(date(2026, 10, 5))
        self.assertEqual(first, date(2027, 2, 8))
        self.assertEqual(second, date(2027, 2, 8))
        self.assertEqual(get.call_count, 2)  # one gToH + one hToG, then served from the cache

    def test_failures_return_none(self):
        for failure in (
            {'side_effect': requests.ConnectionError('down')},
            {'return_value': response({'code': 400, 'status': 'BAD', 'data': 'nope'})},
            {'return_value': response({'unexpected': True})},
            {'return_value': response(status=500)},
        ):
            cache.clear()
            with mock.patch('common.services.hijri_calendar_service.requests.get', **failure):
                self.assertIsNone(self.service.get_next_event(date(2026, 10, 5)), failure)


class HijriUpcomingEventsTests(SimpleTestCase):
    CONVERSIONS = {
        '01-09-1448': '08-02-2027', '27-09-1448': '06-03-2027', '01-10-1448': '09-03-2027',
        '09-12-1448': '15-05-2027', '10-12-1448': '16-05-2027', '01-01-1449': '06-06-2027',
        '10-01-1449': '15-06-2027', '12-03-1449': '14-08-2027',  # Ashura, and Mawlid (the first one past a 300 day horizon)
    }

    def setUp(self):
        cache.clear()
        self.service = HijriCalendarService()

    def upcoming(self, horizon_days, today=date(2026, 10, 5)):
        with mock.patch('common.services.hijri_calendar_service.requests.get', side_effect=aladhan((1448, 4, 24), self.CONVERSIONS)) as get:
            return self.service.get_upcoming_events(today, horizon_days), get

    def test_events_inside_the_horizon_in_date_order(self):
        events, _ = self.upcoming(200)  # to 2027-04-23
        self.assertEqual([(e.name, e.date) for e in events], [
            ('Start of Ramadan', date(2027, 2, 8)), ('Laylat al-Qadr', date(2027, 3, 6)), ('Eid al-Fitr', date(2027, 3, 9))])

    def test_a_short_horizon_finds_nothing(self):
        self.assertEqual(self.upcoming(30)[0], [])

    def test_a_long_horizon_rolls_into_the_next_hijri_year(self):
        events, _ = self.upcoming(300)  # to 2027-08-01
        self.assertEqual([e.name for e in events[-2:]], ['Islamic New Year', 'Ashura'])
        self.assertEqual([e.date for e in events[-2:]], [date(2027, 6, 6), date(2027, 6, 15)])
        self.assertEqual(len(events), 7)

    def test_date_conversions_are_cached(self):
        _, first = self.upcoming(200)
        _, second = self.upcoming(200)
        conversions = lambda get: [c for c in get.call_args_list if '/hToG/' in c.args[0]]  # noqa: E731
        self.assertEqual(len(conversions(first)), 4)    # three events, plus the first one past the horizon
        self.assertEqual(len(conversions(second)), 0)

    def test_an_unreachable_api_gives_an_empty_list(self):
        with mock.patch('common.services.hijri_calendar_service.requests.get', side_effect=requests.ConnectionError('down')):
            self.assertEqual(self.service.get_upcoming_events(date(2026, 10, 5)), [])


# ---------------------------------------------------------------------------------------------
# UK Trade Tariff
# ---------------------------------------------------------------------------------------------

def tariff_fixture():
    return json.loads((FIXTURES / 'tariff_0804100099.json').read_text())


def set_measure(payload, measure_type, geography, **changes):
    """Edit the fixture's measure of this type/geography. ``rate``/``monetary_unit`` change its component."""
    included = {(i['type'], i['id']): i for i in payload['included']}
    for item in payload['included']:
        if item['type'] != 'measure':
            continue
        rel = item['relationships']
        if rel['measure_type']['data']['id'] == measure_type and rel['geographical_area']['data']['id'] == geography:
            for key in ('effective_start_date', 'effective_end_date'):
                if key in changes:
                    item['attributes'][key] = changes[key]
            for ref in rel['measure_components']['data']:
                component = included[('measure_component', ref['id'])]['attributes']
                if 'rate' in changes:
                    component['duty_amount'] = changes['rate']
                if 'monetary_unit' in changes:
                    component['monetary_unit_code'] = changes['monetary_unit']
            return item
    raise AssertionError('measure not found in fixture')


def group_fixture():
    return json.loads((FIXTURES / 'geo_group_1013.json').read_text())


def tariff_get(payload, status=200, groups=None):
    """
    Fake ``requests.get`` for the tariff API. ``groups`` maps a group id to its response (or an exception
    to raise); by default group 1013 (the EU) is served from the real-data fixture.
    """
    groups = {'1013': response(group_fixture())} if groups is None else groups

    def fake_get(url, **kwargs):
        if '/commodities/' in url:
            return response(payload, status)
        group_id = url.rsplit('/', 1)[1]
        outcome = groups.get(group_id, response({'errors': [{'detail': 'not found'}]}, 404))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return fake_get


@override_settings(HMRC_API_URL='https://tariff.test/api/v2')
class HMRCTariffServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.service = HMRCTariffService()

    def duty(self, payload=None, status=200, groups=None, **kwargs):
        payload = payload or tariff_fixture()
        with mock.patch('common.services.hmrctariff_service.requests.get', side_effect=tariff_get(payload, status, groups)) as get:
            return self.service.get_duty('0804100099', **kwargs), get

    def test_standard_third_country_duty(self):
        duty, get = self.duty()
        self.assertEqual(
            duty, TariffDuty('0804100099', 'Other', Decimal('6.0'), '6.00 %', 'third_country', None))
        self.assertEqual(get.call_args.args[0], 'https://tariff.test/api/v2/commodities/0804100099')

    def test_country_preference_is_used_when_origin_is_given(self):
        duty, _ = self.duty(origin='in')  # case-insensitive
        self.assertEqual((duty.rate_percent, duty.source, duty.origin), (Decimal('0.0'), 'preference', 'IN'))

    def test_origin_without_a_preference_gets_the_standard_duty(self):
        duty, _ = self.duty(origin='BR')
        self.assertEqual((duty.rate_percent, duty.source, duty.origin), (Decimal('6.0'), 'third_country', None))

    def test_group_preferences_apply_to_member_countries(self):
        # The measure names the EU (group 1013), not Germany: membership comes from the group endpoint.
        duty, get = self.duty(origin='DE')
        self.assertEqual((duty.rate_percent, duty.source, duty.origin, duty.via), (Decimal('0.0'), 'preference', 'DE', '1013'))
        self.assertIn('https://tariff.test/api/v2/geographical_areas/1013', [c.args[0] for c in get.call_args_list])

    def test_direct_country_preferences_do_not_need_the_group_lookup(self):
        duty, get = self.duty(origin='IN')
        self.assertEqual((duty.source, duty.via), ('preference', 'IN'))
        self.assertTrue(all('/commodities/' in c.args[0] or '1013' in c.args[0] for c in get.call_args_list))

    def test_a_country_outside_the_group_pays_the_standard_duty(self):
        duty, _ = self.duty(origin='US')
        self.assertEqual((duty.rate_percent, duty.source, duty.via), (Decimal('6.0'), 'third_country', None))

    def test_countries_excluded_from_a_group_preference_pay_the_standard_duty(self):
        payload = tariff_fixture()
        eu_measure = set_measure(payload, '142', '1013')
        eu_measure['relationships']['excluded_countries'] = {'data': [{'id': 'DE', 'type': 'geographical_area'}]}
        duty, _ = self.duty(payload, origin='DE')
        self.assertEqual(duty.source, 'third_country')
        cache.clear()
        duty, _ = self.duty(payload, origin='FR')  # the exclusion is specific to Germany
        self.assertEqual(duty.source, 'preference')

    def test_a_failed_group_lookup_falls_back_to_the_standard_duty(self):
        for failure in (requests.ConnectionError('down'), response(status=500), response({'surprise': 1})):
            cache.clear()
            duty, _ = self.duty(origin='DE', groups={'1013': failure})
            self.assertEqual((duty.rate_percent, duty.source), (Decimal('6.0'), 'third_country'), failure)

    def test_group_membership_is_cached(self):
        payload = tariff_fixture()
        with mock.patch('common.services.hmrctariff_service.requests.get', side_effect=tariff_get(payload)) as get:
            self.service.get_duty('0804100099', origin='DE')
            self.service.get_duty('0804100099', origin='FR')
        group_calls = [c for c in get.call_args_list if 'geographical_areas' in c.args[0]]
        self.assertEqual(len(group_calls), 1)

    def test_a_preference_is_ignored_if_it_is_not_lower(self):
        payload = tariff_fixture()
        set_measure(payload, '142', 'IN', rate=9.0)
        duty, _ = self.duty(payload, origin='IN')
        self.assertEqual((duty.rate_percent, duty.source), (Decimal('6.0'), 'third_country'))

    def test_measures_outside_their_validity_dates_are_ignored(self):
        payload = tariff_fixture()
        set_measure(payload, '103', '1011', effective_end_date='2026-01-01T00:00:00.000Z')
        with self.assertRaisesMessage(TariffLookupError, 'No standard duty'):
            self.duty(payload, on=date(2026, 10, 5))
        cache.clear()
        duty, _ = self.duty(payload, on=date(2025, 12, 31))  # still valid back then
        self.assertEqual(duty.rate_percent, Decimal('6.0'))

    def test_duty_that_is_not_a_plain_percentage_has_no_rate(self):
        payload = tariff_fixture()
        set_measure(payload, '103', '1011', monetary_unit='GBP')  # e.g. "6 % + 15 GBP / 100 kg"
        duty, _ = self.duty(payload)
        self.assertIsNone(duty.rate_percent)
        self.assertEqual(duty.expression, '6.00 %')

    def test_unknown_commodity_is_a_clear_error(self):
        with self.assertRaisesMessage(TariffLookupError, 'not a declarable commodity code'):
            self.duty({'errors': [{'detail': 'not found'}]}, status=404)

    def test_service_down_or_garbled_is_a_lookup_error(self):
        with mock.patch('common.services.hmrctariff_service.requests.get', side_effect=requests.ConnectionError('down')):
            with self.assertRaisesMessage(TariffLookupError, 'Could not reach'):
                self.service.get_duty('0804100099')
        with mock.patch('common.services.hmrctariff_service.requests.get', return_value=response({'surprise': 1})):
            with self.assertRaisesMessage(TariffLookupError, 'Unexpected response'):
                self.service.get_duty('0804100099')
        with mock.patch('common.services.hmrctariff_service.requests.get', return_value=response(status=500)):
            with self.assertRaises(TariffLookupError):
                self.service.get_duty('0804100099')

    def test_commodity_code_must_be_ten_digits(self):
        for bad in ('', '123', '08041000999', 'abcdefghij', None):
            with self.assertRaises(ValueError):
                self.service.get_duty(bad)

    def test_responses_are_cached(self):
        payload = tariff_fixture()
        with mock.patch('common.services.hmrctariff_service.requests.get', side_effect=tariff_get(payload)) as get:
            self.service.get_duty('0804100099')
            self.service.get_duty('0804100099', origin='IN')
        commodity_calls = [c for c in get.call_args_list if '/commodities/' in c.args[0]]
        self.assertEqual(len(commodity_calls), 1)

    def test_the_highest_of_several_standard_duties_is_used(self):
        payload = copy.deepcopy(tariff_fixture())
        original = set_measure(payload, '103', '1011')
        clone = copy.deepcopy(original)
        clone['id'] = '999999'
        payload['included'].append(clone)
        for item in copy.deepcopy(payload['included']):
            if item['type'] == 'measure_component' and item['id'] in [c['id'] for c in original['relationships']['measure_components']['data']]:
                twin = copy.deepcopy(item)
                twin['id'] = 'twin'
                twin['attributes']['duty_amount'] = 12.0
                payload['included'].append(twin)
        clone['relationships']['measure_components'] = {'data': [{'id': 'twin', 'type': 'measure_component'}]}
        payload['data']['relationships']['import_measures']['data'].append({'id': '999999', 'type': 'measure'})
        duty, _ = self.duty(payload)
        self.assertEqual(duty.rate_percent, Decimal('12.0'))


@override_settings(DEFAULT_CUSTOMS_DUTY_RATE=0.05)
class CostCalculationServiceTests(SimpleTestCase):
    def setUp(self):
        self.tariff = mock.Mock(spec=HMRCTariffService)
        self.service = CostCalculationService(tariff_service=self.tariff)
        self.product = mock.Mock(price=Decimal('10.00'), commodity_code='0804100099')

    def duty(self, rate, source='third_country', expression='x'):
        return TariffDuty('0804100099', 'Other', rate, expression, source, 'IN' if source == 'preference' else None)

    def test_uses_the_real_tariff_rate(self):
        self.tariff.get_duty.return_value = self.duty(Decimal('6.0'))
        cost = self.service.landed_cost_breakdown(self.product, 2)
        self.assertEqual((cost.goods_cost, cost.duty_rate, cost.duty_amount, cost.total, cost.duty_source),
                         (Decimal('20.00'), Decimal('0.06'), Decimal('1.20'), Decimal('21.20'), 'tariff'))
        self.assertEqual(self.service.calculate_landed_cost(self.product, 2), Decimal('21.20'))

    def test_origin_is_passed_through_and_reported(self):
        self.tariff.get_duty.return_value = self.duty(Decimal('0.0'), source='preference')
        cost = self.service.landed_cost_breakdown(self.product, 2, origin='IN')
        self.tariff.get_duty.assert_called_once_with('0804100099', origin='IN')
        self.assertEqual((cost.duty_amount, cost.duty_source), (Decimal('0.00'), 'tariff_preference'))

    def test_falls_back_to_the_default_rate_and_says_why(self):
        self.tariff.get_duty.side_effect = TariffLookupError('service down')
        cost = self.service.landed_cost_breakdown(self.product, 2)
        self.assertEqual((cost.duty_rate, cost.total, cost.duty_source, cost.note), (Decimal('0.05'), Decimal('21.00'), 'default', 'service down'))

    def test_no_commodity_code_uses_the_default(self):
        self.product.commodity_code = ''
        cost = self.service.landed_cost_breakdown(self.product, 1)
        self.assertEqual((cost.duty_source, cost.note), ('default', 'Product has no commodity code.'))
        self.tariff.get_duty.assert_not_called()

    def test_specific_duties_use_the_default_rate(self):
        self.tariff.get_duty.return_value = self.duty(None, expression='6.00 % + 15.00 GBP / 100 kg')
        cost = self.service.landed_cost_breakdown(self.product, 1)
        self.assertEqual(cost.duty_source, 'default')
        self.assertIn('not a plain percentage', cost.note)

    def test_invalid_commodity_code_uses_the_default(self):
        self.tariff.get_duty.side_effect = ValueError('Commodity code must be exactly 10 digits.')
        self.assertEqual(self.service.landed_cost_breakdown(self.product, 1).duty_source, 'default')


# ---------------------------------------------------------------------------------------------
# Shopify (GraphQL Admin API)
# ---------------------------------------------------------------------------------------------

def variants_page(ids, has_next=False, cursor=None):
    return response({'data': {'product': {'variants': {
        'nodes': [{'id': i} for i in ids], 'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor}}}}})


def update_result(ids, price='9.99', errors=()):
    return response({'data': {'productVariantsBulkUpdate': {
        'productVariants': [{'id': i, 'price': price} for i in ids], 'userErrors': list(errors)}}})


@override_settings(SHOPIFY_API_VERSION='2026-10')
class ShopifyServiceTests(TestCase):
    def setUp(self):
        self.connection = ShopifyConnectionFactory(shop_domain='my-store.myshopify.com', access_token='shpat_secret')
        self.service = ShopifyService('my-store.myshopify.com')

    def post(self, *responses):
        return mock.patch('common.services.shopify_service.requests.post', side_effect=list(responses))

    def test_unknown_shop(self):
        with self.assertRaisesMessage(ValueError, 'No Shopify connection'):
            ShopifyService('other-store.myshopify.com')

    def test_only_real_shopify_domains_are_accepted(self):
        for bad in ('', 'evil.com', 'attacker.com/x.myshopify.com', 'my-store.myshopify.com.evil.com',
                    'http://my-store.myshopify.com', 'my store.myshopify.com', 'internal:8080.myshopify.com',
                    'My-Store.myshopify.com', '-x.myshopify.com', None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ShopifyService(bad)

    def test_updates_every_variant_through_the_graphql_api(self):
        ids = ['gid://shopify/ProductVariant/1', 'gid://shopify/ProductVariant/2']
        with self.post(variants_page(ids), update_result(ids, '9.50')) as post:
            updated = self.service.update_product_price(555, Decimal('9.5'))
        self.assertEqual([v['price'] for v in updated], ['9.50', '9.50'])

        query_call, mutation_call = post.call_args_list
        self.assertEqual(query_call.args[0], 'https://my-store.myshopify.com/admin/api/2026-10/graphql.json')
        self.assertEqual(query_call.kwargs['headers']['X-Shopify-Access-Token'], 'shpat_secret')
        self.assertEqual(query_call.kwargs['timeout'], 10)
        self.assertEqual(query_call.kwargs['json']['variables']['id'], 'gid://shopify/Product/555')
        self.assertIn('productVariantsBulkUpdate', mutation_call.kwargs['json']['query'])
        self.assertEqual(mutation_call.kwargs['json']['variables'], {
            'productId': 'gid://shopify/Product/555',
            'variants': [{'id': ids[0], 'price': '9.50'}, {'id': ids[1], 'price': '9.50'}],
        })

    def test_follows_variant_pagination(self):
        with self.post(variants_page(['v1'], True, 'cursor-1'), variants_page(['v2']), update_result(['v1', 'v2'])) as post:
            self.service.update_product_price(1, Decimal('1'))
        self.assertEqual(post.call_args_list[1].kwargs['json']['variables']['after'], 'cursor-1')
        self.assertEqual(len(post.call_args_list[2].kwargs['json']['variables']['variants']), 2)

    def test_large_products_are_updated_in_batches_of_100(self):
        ids = [f'v{i}' for i in range(150)]
        with self.post(variants_page(ids), update_result(ids[:100]), update_result(ids[100:])) as post:
            updated = self.service.update_product_price(1, Decimal('2'))
        self.assertEqual(len(updated), 150)
        sizes = [len(c.kwargs['json']['variables']['variants']) for c in post.call_args_list[1:]]
        self.assertEqual(sizes, [100, 50])

    def test_shopify_rejections_are_errors(self):
        cases = {
            'user errors': [variants_page(['v1']), update_result([], errors=[{'field': ['price'], 'message': 'bad'}])],
            'graphql errors': [response({'errors': [{'message': 'Throttled'}]})],
            'missing product': [response({'data': {'product': None}})],
            'no variants': [variants_page([])],
            'http error': [response(status=401)],
        }
        for name, responses in cases.items():
            with self.post(*responses), self.assertRaises(ShopifyError, msg=name):
                self.service.update_product_price(1, Decimal('1'))

    def test_network_failure_is_a_shopify_error(self):
        with mock.patch('common.services.shopify_service.requests.post', side_effect=requests.Timeout('slow')):
            with self.assertRaises(ShopifyError):
                self.service.update_product_price(1, Decimal('1'))
