# config/common/views.py

from rest_framework.viewsets import ModelViewSet
from .models import GenericModel  # Assuming you have a base model for common views
from .serializers import GenericSerializer  # Corresponding serializer

class CommonViewSet(ModelViewSet):
    """
    ViewSet for handling CRUD operations on generic models.
    """
    queryset = GenericModel.objects.all()
    serializer_class = GenericSerializer

# This viewset can be used to manage any model that inherits from GenericModel