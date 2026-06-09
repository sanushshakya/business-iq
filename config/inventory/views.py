# inventory/views.py

from rest_framework.viewsets import ModelViewSet
from .models import InventoryItem
from .serializers import InventoryItemSerializer

class InventoryItemViewSet(ModelViewSet):
    """
    API endpoint that allows inventory items to be viewed or edited.
    """

    queryset = InventoryItem.objects.all()
    serializer_class = InventoryItemSerializer