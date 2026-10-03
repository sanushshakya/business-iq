# demand/views.py

from rest_framework import viewsets

from common.tenancy import SharedReferenceMixin, TenantScopedMixin

from .models import Demand, CulturalEvent, EventProductKeyword
from .serializers import DemandSerializer, CulturalEventSerializer, EventProductKeywordSerializer


class DemandViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Demand.objects.all()
    serializer_class = DemandSerializer


class CulturalEventViewSet(SharedReferenceMixin, viewsets.ModelViewSet):
    queryset = CulturalEvent.objects.all()
    serializer_class = CulturalEventSerializer


class EventProductKeywordViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = EventProductKeyword.objects.all()
    serializer_class = EventProductKeywordSerializer
