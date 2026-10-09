# common/services/hijri_calendar_service.py

"""
Upcoming Islamic dates (Ramadan, the two Eids, ...) for demand planning, via the public AlAdhan API
(https://aladhan.com/islamic-calendar-api). No API key is needed.

The API converts between Gregorian and Hijri dates. This service holds the list of major events as fixed
Hijri (month, day) pairs and asks the API where each next falls in the Gregorian calendar. Dates follow
the API's default (Umm al-Qura) calendar, so actual moon-sighting dates can differ by a day.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# (Hijri month, Hijri day, name). Days are all <= 29, so they exist in every month: the API silently
# "normalises" impossible dates instead of rejecting them.
MAJOR_EVENTS = (
    (1, 1, 'Islamic New Year'),
    (1, 10, 'Ashura'),
    (3, 12, 'Mawlid'),
    (9, 1, 'Start of Ramadan'),
    (9, 27, 'Laylat al-Qadr'),
    (10, 1, 'Eid al-Fitr'),
    (12, 9, 'Day of Arafah'),
    (12, 10, 'Eid al-Adha'),
)

API_DATE_FORMAT = '%d-%m-%Y'


@dataclass(frozen=True)
class HijriEvent:
    name: str
    date: date


class HijriCalendarService:
    CACHE_TIMEOUT = 6 * 60 * 60  # seconds; the answer only changes at midnight

    def _get(self, path):
        response = requests.get(f"{settings.ALADHAN_API_URL}/{path}", timeout=settings.HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        if payload.get('code') != 200:
            raise ValueError(f"AlAdhan returned {payload.get('code')}: {payload.get('data')}")
        return payload['data']

    def hijri_today(self, today: Optional[date] = None):
        """Return today's Hijri date as ``(year, month, day)`` integers."""
        today = today or date.today()
        hijri = self._get(f"gToH/{today.strftime(API_DATE_FORMAT)}")['hijri']
        return int(hijri['year']), int(hijri['month']['number']), int(hijri['day'])

    def to_gregorian(self, year: int, month: int, day: int) -> date:
        gregorian = self._get(f"hToG/{day:02d}-{month:02d}-{year}")['gregorian']['date']
        return datetime.strptime(gregorian, API_DATE_FORMAT).date()

    def _cached_gregorian(self, year: int, month: int, day: int) -> date:
        key = f"hijri:gregorian:{year}-{month:02d}-{day:02d}"
        found = cache.get(key)
        if found is None:
            found = self.to_gregorian(year, month, day)
            cache.set(key, found, 30 * 24 * 60 * 60)  # a date conversion never changes
        return found

    def get_upcoming_events(self, today: Optional[date] = None, horizon_days: int = 120) -> list:
        """All major events from ``today`` up to ``horizon_days`` ahead, in date order ([] if the API is down)."""
        today = today or date.today()
        horizon = today + timedelta(days=horizon_days)
        try:
            year, month, day = self.hijri_today(today)
            candidates = [(year, m, d, name) for m, d, name in MAJOR_EVENTS if (m, d) >= (month, day)]
            candidates += [(year + 1, m, d, name) for m, d, name in MAJOR_EVENTS]
            events = []
            for event_year, event_month, event_day, name in candidates:
                event_date = self._cached_gregorian(event_year, event_month, event_day)
                if event_date > horizon:
                    break  # candidates are in calendar order
                if event_date >= today:
                    events.append(HijriEvent(name=name, date=event_date))
            return events
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            logger.warning("Error fetching upcoming Hijri events: %s", exc)
            return []

    def get_next_event(self, today: Optional[date] = None) -> Optional[HijriEvent]:
        """
        The next major event on or after ``today``, or None if the API cannot be reached.
        """
        today = today or date.today()
        cache_key = f"hijri:next-event:{today.isoformat()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            year, month, day = self.hijri_today(today)
            # Events still to come this Hijri year, then next year's, in calendar order.
            upcoming = [(year, m, d, name) for m, d, name in MAJOR_EVENTS if (m, d) >= (month, day)]
            upcoming += [(year + 1, m, d, name) for m, d, name in MAJOR_EVENTS]
            for event_year, event_month, event_day, name in upcoming:
                event_date = self.to_gregorian(event_year, event_month, event_day)
                if event_date >= today:  # guards against the two calendars disagreeing by a day
                    event = HijriEvent(name=name, date=event_date)
                    cache.set(cache_key, event, self.CACHE_TIMEOUT)
                    return event
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            logger.warning("Error fetching next Hijri event: %s", exc)
        return None

    def get_next_event_date(self, today: Optional[date] = None) -> Optional[date]:
        """Convenience wrapper around ``get_next_event`` returning only the date."""
        event = self.get_next_event(today)
        return event.date if event else None
