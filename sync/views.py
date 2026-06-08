"""
config/sync/views.py

This file contains Django REST Framework (DRF) ViewSets for handling sync operations in the `sync` app of the `iq` project.
"""

from rest_framework import viewsets
from .models import SyncModel  # Replace with actual model name
from .serializers import SyncSerializer  # Replace with actual serializer name

class SyncViewSet(viewsets.ModelViewSet):
    """
    A viewset for handling CRUD operations on the SyncModel.

    This ViewSet provides default actions for list, create, retrieve, update, and destroy.
    """

    queryset = SyncModel.objects.all()
    serializer_class = SyncSerializer
```

This file defines a DRF `ViewSet` named `SyncViewSet` for managing operations on a hypothetical model `SyncModel`. The viewset includes standard actions for listing all instances, creating new instances, retrieving a specific instance, updating an existing instance, and deleting an instance.