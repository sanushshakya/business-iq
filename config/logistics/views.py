from rest_framework import viewsets
from .models import LogisticsOrder, WarehouseLocation, TransportVehicle
from .serializers import LogisticsOrderSerializer, WarehouseLocationSerializer, TransportVehicleSerializer

class LogisticsOrderViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing logistics order instances.
    """
    queryset = LogisticsOrder.objects.all()
    serializer_class = LogisticsOrderSerializer


class WarehouseLocationViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing warehouse location instances.
    """
    queryset = WarehouseLocation.objects.all()
    serializer_class = WarehouseLocationSerializer


class TransportVehicleViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing transport vehicle instances.
    """
    queryset = TransportVehicle.objects.all()
    serializer_class = TransportVehicleSerializer