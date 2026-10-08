# common/services/hmrctariff_service.py

"""
Import duty lookup via the public UK Trade Tariff API (https://www.trade-tariff.service.gov.uk/api/v2).
No API key is needed.

A commodity is identified by its full 10 digit code (e.g. ``0804100099``, "Dates, other"). For each
commodity the API lists every import measure; the ones used here are:

* ``103`` "Third country duty" for ``ERGA OMNES`` (everyone): the standard duty, and
* ``142`` "Tariff preference" for a specific country: the reduced duty a trade deal gives that country.

Preferences are given to single countries and to groups (the EU, the Developing Countries Trading Scheme, ...).
For a group, its member countries are looked up from ``/geographical_areas/{group}`` (cached for a week); if that
lookup fails the group is skipped, so the result is the safe, higher, standard duty. Duties that are not a plain
percentage (e.g. "12 % + 15 GBP / 100 kg") are reported with ``rate_percent=None``. Preferences only apply with
valid proof of origin, so only pass ``origin`` when the importer qualifies.
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
COUNTRY_CODE = re.compile(r'^[A-Z]{2}$')  # anything else in a measure's geography is a group


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
    via: Optional[str] = None        # the country or group (e.g. '1013' = EU) the preference came from


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

    def fetch_group_members(self, group_id: str) -> frozenset:
        """ISO codes of the countries in a geographical group (e.g. '1013' = EU), cached for a week."""
        cache_key = f"hmrc:group:{group_id}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            response = requests.get(
                f"{self.base_url}/geographical_areas/{group_id}", timeout=self.timeout, headers={'Accept': 'application/json'}
            )
            response.raise_for_status()
            payload = response.json()
            members = frozenset(i['id'] for i in payload.get('included', []) if i['type'] == 'geographical_area')
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            raise TariffLookupError(f"Could not read group {group_id}: {exc}") from exc
        cache.set(cache_key, members, 7 * 24 * 60 * 60)
        return members

    def _applies_to(self, measure: dict, origin: str) -> bool:
        """Does this preference measure cover ``origin``, directly or through a group it belongs to?"""
        if origin in measure['excluded']:
            return False
        geography = measure['geography']
        if COUNTRY_CODE.match(geography):
            return geography == origin
        try:
            return origin in self.fetch_group_members(geography)
        except TariffLookupError as exc:
            logger.warning("Ignoring preference for group %s: %s", geography, exc)
            return False

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

        via = None
        if origin:
            origin = origin.upper()
            preferences = [
                m for m in active
                if m['type'] == TARIFF_PREFERENCE and m['rate_percent'] is not None and self._applies_to(m, origin)
            ]
            best = min(preferences, key=lambda m: Decimal(m['rate_percent'])) if preferences else None
            if best and (chosen['rate_percent'] is None or Decimal(best['rate_percent']) < Decimal(chosen['rate_percent'])):
                chosen, source, applied_origin, via = best, 'preference', origin, best['geography']

        rate = Decimal(chosen['rate_percent']) if chosen['rate_percent'] is not None else None
        return TariffDuty(
            commodity_code=commodity_code,
            description=data['description'],
            rate_percent=rate,
            expression=chosen['expression'],
            source=source,
            origin=applied_origin,
            via=via,
        )

    @staticmethod
    def _highest(measures):
        percentages = [m for m in measures if m['rate_percent'] is not None]
        return max(percentages, key=lambda m: Decimal(m['rate_percent'])) if percentages else measures[0]
