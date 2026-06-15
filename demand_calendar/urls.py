from django.urls import path

# Module docstring: Defines URL patterns for accessing the Demand Calendar API.

urlpatterns = [
    # Endpoint to retrieve events for the next 3 months
    path('events/', 'demand_calendar.views.get_next_three_months_events', name='get_next_three_months_events'),
]
```

This file, `demand_calendar/urls.py`, configures the URL patterns for the Demand Calendar API. The endpoint `/events/` is set up to call a view function named `get_next_three_months_events` from the `demand_calendar.views` module when accessed.