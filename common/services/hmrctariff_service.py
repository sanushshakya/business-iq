# common/services/hmrctariff_service.py

"""
Import duty lookup via the public UK Trade Tariff API (https://www.trade-tariff.service.gov.uk/api/v2).
No API key is needed.

A commodity is identified by its full 10 digit code (e.g. ``0804100099``, "Dates, other"). For each
commodity the API lists every import measure; the ones used here are:

* ``103`` "Third country duty" for ``ERGA OMNES`` (everyone): the standard duty, and
* ``142`` "Tariff preference" for a specific country: the reduced duty a trade deal gives that country.

Limits: duty groups such as the EU or the Developing Countries Trading Scheme are not expanded into their
member countries (a preference is only found when the origin country itself is named in the measure), and
duties that are not a plain percentage (e.g. "12 % + 15 GBP / 100 kg") are reported with ``rate_percent=None``.
Preferences only apply with valid proof of origin, so only pass ``origin`` when the importer qualifies.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

THIRD_COUNTRY_DUTY = '103'
TARIFF_PREFERENCE = '142'
ERGA_OMNES = '1011'            # the "all countries" geographical area
AD_VALOREM = '01'              # duty expression: "% or amount"
COMMODITY_CODE = re.compile(r'^\d{10}$')


class TariffLookupError(Exception):
    """The tariff could not be retrieved or contains no usable duty for the commodity."""


@dataclass(frozen=True)
class TariffDuty:
    commodity_code: str
    description: str
    rate_percent: Optional[Decimal]  # None when the duty is not a plain percentage
    expression: str                  # as published, e.g. "6.00 %"
    source: str                      # 'third_country' or 'preference'
    origin: Optional[str]            # set when a country preference was applied


class HMRCTariffService:
    CACHE_TIMEOUT = 24 * 60 * 60  # seconds; tariffs change rarely

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[int] = None):
        self.base_url = (base_url or settings.HMRC_API_URL).rstrip('/')
        self.timeout = timeout or settings.HTTP_TIMEOUT_SECONDS

    # ---- fetching -------------------------------------------------------------------------

    def fetch_measures(self, commodity_code: str) -> dict:
        """
        Return ``{"description": str, "measures": [...]}`` for a commodity, cached for a day.

        Each measure is a small dict (type, geographical area, validity dates, duty expression and
        whether it is a plain percentage) so only the useful part of the large API response is cached.
        """
        if not COMMODITY_CODE.match(commodity_code or ''):
            raise ValueError("Commodity code must be exactly 10 digits.")

        cache_key = f"hmrc:tariff:{commodity_code}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        url = f"{self.base_url}/commodities/{commodity_code}"
        try:
            response = requests.get(url, timeout=self.timeout, headers={'Accept': 'application/json'})
        except requests.RequestException as exc:
            raise TariffLookupError(f"Could not reach the tariff service: {exc}") from exc
        if response.status_code == 404:
            raise TariffLookupError(f"{commodity_code} is not a declarable commodity code.")
        try:
            response.raise_for_status()
            result = self._parse(response.json())
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            raise TariffLookupError(f"Unexpected response from the tariff service: {exc}") from exc

        cache.set(cache_key, result, self.CACHE_TIMEOUT)
        return result

    @staticmethod
    def _parse(payload: dict) -> dict:
        included = {(item['type'], item['id']): item for item in payload.get('included', [])}

        def related(measure, relationship):
            data = measure['relationships'].get(relationship, {}).get('data')
            return included.get((data['type'], data['id'])) if data else None

        measures = []
        for ref in payload['data']['relationships']['import_measures']['data']:
            measure = included[('measure', ref['id'])]
            components = [
                included[('measure_component', c['id'])]['attributes']
                for c in measure['relationships'].get('measure_components', {}).get('data', [])
            ]
            if not components:
                continue  # prohibitions, import controls, ...: no duty
            plain_percentage = all(
                c['duty_expression_id'] == AD_VALOREM and not c.get('monetary_unit_code') and not c.get('measurement_unit_code')
                for c in components
            )
            expression = related(measure, 'duty_expression')
            measures.append({
                'type': measure['relationships']['measure_type']['data']['id'],
                'geography': measure['relationships']['geographical_area']['data']['id'],
                'start': (measure['attributes'].get('effective_start_date') or '')[:10] or None,
                'end': (measure['attributes'].get('effective_end_date') or '')[:10] or None,
                'expression': expression['attributes'].get('base', '') if expression else '',
                'rate_percent': str(sum(Decimal(str(c['duty_amount'])) for c in components)) if plain_percentage else None,
                'excluded': [x['id'] for x in measure['relationships'].get('excluded_countries', {}).get('data', [])],
            })
        return {'description': payload['data']['attributes'].get('description', ''), 'measures': measures}

    # ---- choosing the duty ---------------------------------------------------------------

    def get_duty(self, commodity_code: str, origin: Optional[str] = None, on: Optional[date] = None) -> TariffDuty:
        """
        The import duty for a commodity on ``on`` (default today).

        Without ``origin`` this is the standard third-country duty. With an ISO country code
        (e.g. ``"IN"``) a lower tariff preference for that country is used when one exists.
        """
        on = (on or date.today()).isoformat()
        data = self.fetch_measures(commodity_code)
        active = [m for m in data['measures'] if (m['start'] or '0000') <= on and (not m['end'] or m['end'] >= on)]

        standard = [m for m in active if m['type'] == THIRD_COUNTRY_DUTY and m['geography'] == ERGA_OMNES]
        if not standard:
            raise TariffLookupError(f"No standard duty is published for {commodity_code}.")
        chosen = self._highest(standard)  # several can exist; be conservative and take the highest
        source, applied_origin = 'third_country', None

        if origin:
            origin = origin.upper()
            preferences = [
                m for m in active
                if m['type'] == TARIFF_PREFERENCE and m['geography'] == origin and origin not in m['excluded']
                and m['rate_percent'] is not None
            ]
            best = min(preferences, key=lambda m: Decimal(m['rate_percent'])) if preferences else None
            if best and (chosen['rate_percent'] is None or Decimal(best['rate_percent']) < Decimal(chosen['rate_percent'])):
                chosen, source, applied_origin = best, 'preference', origin

        rate = Decimal(chosen['rate_percent']) if chosen['rate_percent'] is not None else None
        return TariffDuty(
            commodity_code=commodity_code,
            description=data['description'],
            rate_percent=rate,
            expression=chosen['expression'],
            source=source,
            origin=applied_origin,
        )

    @staticmethod
    def _highest(measures):
        percentages = [m for m in measures if m['rate_percent'] is not None]
        return max(percentages, key=lambda m: Decimal(m['rate_percent'])) if percentages else measures[0]
