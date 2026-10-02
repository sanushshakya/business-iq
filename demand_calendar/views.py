# demand_calendar/views.py

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import DemandAlert, Event
from .serializers import DemandAlertSerializer, EventSerializer


class EventViewSet(viewsets.ModelViewSet):
    queryset = Event.objects.all()
    serializer_class = EventSerializer


class DemandAlertViewSet(viewsets.ModelViewSet):
    queryset = DemandAlert.objects.all()
    serializer_class = DemandAlertSerializer


@extend_schema(responses={200: dict})
@api_view(['GET'])
def get_next_three_months_events(request):
    """
    Events overlapping the next 90 days, grouped by the month they start in.
    """
    today = timezone.now().date()
    window_end = today + timedelta(days=90)
    events = (
        Event.objects.filter(end_date__gte=today, start_date__lte=window_end)
        .prefetch_related('product_categories', 'demand_alerts')
        .order_by('start_date')
    )

    months = []
    for event in events:
        label = event.start_date.strftime('%B %Y')
        if not months or months[-1]['month'] != label:
            months.append({'month': label, 'events': []})
        months[-1]['events'].append({
            'name': event.name,
            'date_range': f"{event.start_date.strftime('%d %b %Y')} - {event.end_date.strftime('%d %b %Y')}",
            'product_categories': [category.name for category in event.product_categories.all()],
            'demand_multiplier': event.demand_multiplier,
            'demand_alert_link': next((a.url for a in event.demand_alerts.all()), None),
        })
    return Response(months)
