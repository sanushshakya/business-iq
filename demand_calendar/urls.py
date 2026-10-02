from django.urls import path

# Module docstring: Defines URL patterns for accessing the Demand Calendar API.

urlpatterns = [
    # Endpoint to retrieve events for the next 3 months
    path('events/', 'demand_calendar.views.get_next_three_months_events', name='get_next_three_months_events'),
]
