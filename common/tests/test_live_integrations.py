# common/tests/test_live_integrations.py

"""
Contract checks against the REAL public services (AlAdhan and the UK Trade Tariff). They are skipped
unless RUN_LIVE_TESTS=1, so the normal suite stays offline and deterministic:

    RUN_LIVE_TESTS=1 pytest common/tests/test_live_integrations.py

Shopify is not covered: it needs a real store and access token.
"""

import os
import unittest
from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.test import SimpleTestCase

from common.services.hijri_calendar_service import MAJOR_EVENTS, HijriCalendarService
from common.services.hmrctariff_service import HMRCTariffService, TariffLookupError


@unittest.skipUnless(os.environ.get('RUN_LIVE_TESTS') == '1', 'set RUN_LIVE_TESTS=1 to call the real services')
class LiveIntegrationTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_hijri_next_event_is_a_future_major_event(self):
        event = HijriCalendarService().get_next_event()
        self.assertIsNotNone(event, 'AlAdhan unreachable or its response format changed')
        self.assertIn(event.name, {name for _, _, name in MAJOR_EVENTS})
        self.assertGreaterEqual(event.date, date.today())

    def test_hijri_known_conversion(self):
        # 1 Ramadan 1448 AH
        self.assertEqual(HijriCalendarService().to_gregorian(1448, 9, 1), date(2027, 2, 8))

    def test_tariff_standard_duty_for_dates(self):
        duty = HMRCTariffService().get_duty('0804100099')
        self.assertEqual(duty.source, 'third_country')
        self.assertIsInstance(duty.rate_percent, Decimal)
        self.assertGreaterEqual(duty.rate_percent, 0)

    def test_tariff_unknown_code_is_reported(self):
        with self.assertRaises(TariffLookupError):
            HMRCTariffService().get_duty('0804100000')
