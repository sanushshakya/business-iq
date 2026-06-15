from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta
from .models import Event

def get_next_3_months_events(request):
    """
    API endpoint to fetch the next 3 months' events.

    Returns:
        JsonResponse: A JSON response containing a list of events for the next 3 months.
    """

    # Calculate the start and end dates for the next 3 months
    today = timezone.now().date()
    three_months_later = today + timedelta(days=90)
    
    # Fetch events within the next 3 months
    events = Event.objects.filter(date__gte=today, date__lte=three_months_later).order_by('date')

    # Prepare event data for JSON response
    event_data = []
    current_month = None
    for event in events:
        if event.date.month != current_month:
            current_month = event.date.month
            event_data.append({
                'month': event.date.strftime('%B %Y'),
                'events': []
            })
        
        event_data[-1]['events'].append({
            'name': event.name,
            'date_range': f"{event.date.strftime('%d %b %Y')} - {event.end_date.strftime('%d %b %Y')}",
            'product_categories': [category.name for category in event.categories.all()],
            'demand_multiplier': event.demand_multiplier,
            'demand_alert_link': event.demand_alert_link
        })

    return JsonResponse(event_data, safe=False)