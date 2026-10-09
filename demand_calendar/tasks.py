# demand_calendar/tasks.py

from datetime import timedelta
from decimal import Decimal

from celery import shared_task

from common.services.hijri_calendar_service import HijriCalendarService

from .models import Event

# How long each event lasts (days) and how much it lifts demand. Staff can refine both, and assign the
# product categories an event affects (alerts are only raised for events that have categories).
EVENT_DEFAULTS = {
    'Start of Ramadan': (30, Decimal('1.50')),
    'Eid al-Fitr': (3, Decimal('1.80')),
    'Day of Arafah': (1, Decimal('1.20')),
    'Eid al-Adha': (4, Decimal('1.80')),
}


@shared_task
def sync_islamic_events(horizon_days=120):
    """
    Add the major Islamic events of the next ``horizon_days`` days to the demand calendar.

    Existing events (same name and start date) are left untouched, so edits made by staff are kept.
    Returns the number of events created.
    """
    created = 0
    for hijri_event in HijriCalendarService().get_upcoming_events(horizon_days=horizon_days):
        days, multiplier = EVENT_DEFAULTS.get(hijri_event.name, (1, Decimal('1.00')))
        _, was_created = Event.objects.get_or_create(
            name=hijri_event.name,
            start_date=hijri_event.date,
            defaults={'end_date': hijri_event.date + timedelta(days=days - 1), 'demand_multiplier': multiplier},
        )
        created += was_created
    return created
