from rest_framework import viewsets

from .models import VatRate
from .serializers import VatRateSerializer


class VatRateViewSet(viewsets.ReadOnlyModelViewSet):
    """UK VAT rates by code and start date (read-only; superusers change them in the admin)."""

    queryset = VatRate.objects.all().order_by('code', '-valid_from', 'id')
    serializer_class = VatRateSerializer
