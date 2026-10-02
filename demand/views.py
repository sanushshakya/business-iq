# demand/views.py

from rest_framework import viewsets

from common.tenancy import IsStaffOrReadOnly, TenantScopedMixin

from .models import Demand, CulturalEvent, EventProductKeyword
from .serializers import DemandSerializer, CulturalEventSerializer, EventProductKeywordSerializer


class DemandViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = Demand.objects.all()
    serializer_class = DemandSerializer


class CulturalEventViewSet(viewsets.ModelViewSet):
    permission_classes = [IsStaffOrReadOnly]
    queryset = CulturalEvent.objects.all()
    serializer_class = CulturalEventSerializer


class EventProductKeywordViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = EventProductKeyword.objects.all()
    serializer_class = EventProductKeywordSerializer
