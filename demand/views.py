# demand/views.py

from rest_framework import viewsets

from .models import Demand, CulturalEvent, EventProductKeyword
from .serializers import DemandSerializer, CulturalEventSerializer, EventProductKeywordSerializer


class DemandViewSet(viewsets.ModelViewSet):
    queryset = Demand.objects.all()
    serializer_class = DemandSerializer


class CulturalEventViewSet(viewsets.ModelViewSet):
    queryset = CulturalEvent.objects.all()
    serializer_class = CulturalEventSerializer


class EventProductKeywordViewSet(viewsets.ModelViewSet):
    queryset = EventProductKeyword.objects.all()
    serializer_class = EventProductKeywordSerializer
