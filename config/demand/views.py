from rest_framework import viewsets
from .models import Demand
from .serializers import DemandSerializer

class DemandViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows demands to be viewed or edited.
    """
    queryset = Demand.objects.all()
    serializer_class = DemandSerializer

    def perform_create(self, serializer):
        """
        Override the create method to set the tenant for the demand.
        """
        serializer.save(tenant=self.request.tenant)