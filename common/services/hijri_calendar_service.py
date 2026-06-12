# common/services/hijri_calendar_service.py
"""
Service layer for interacting with the AlAdhan.com Hijri calendar API to fetch event dates and manage caching.
"""

import requests
from django.core.cache import cache
from django.utils.decorators import method_decorator

class HijriCalendarService:
    """
    Service class responsible for fetching next event date from the AlAdhan.com Hijri calendar API.
    """

    BASE_URL = "https://aladhan.com"
    CACHE_KEY = "next_hijri_event_date"
    CACHE_TIMEOUT = 3600  # Cache timeout in seconds (1 hour)

    @method_decorator(cache_page(CACHE_TIMEOUT))
    def get_next_event_date(self):
        """
        Fetches the next event date from the AlAdhan.com Hijri calendar API.
        
        Returns:
            datetime.date: The date of the next hijri event if found, otherwise None.
        """
        # Check cache for the next event date
        next_event_date = cache.get(HijriCalendarService.CACHE_KEY)
        if next_event_date is not None:
            return next_event_date

        try:
            response = requests.get(f"{HijriCalendarService.BASE_URL}/calendar.json?method=2")
            response.raise_for_status()
            data = response.json()

            # Extract the next event date from the API response
            events = data.get("data", {}).get("events", [])
            if events:
                next_event_date_str = events[0].get("date", {}).get("hijri", {}).get("readable")
                if next_event_date_str:
                    from datetime import datetime
                    # Parse the date string to a datetime object
                    next_event_date = datetime.strptime(next_event_date_str, "%d %B %Y").date()
                    # Cache the event date for future use
                    cache.set(HijriCalendarService.CACHE_KEY, next_event_date, HijriCalendarService.CACHE_TIMEOUT)
                    return next_event_date

        except requests.RequestException as e:
            print(f"Error fetching next hijri event date: {e}")

        return None