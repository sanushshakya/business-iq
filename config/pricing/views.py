"""
config/pricing/views.py

This file contains Django REST Framework (DRF) ViewSets for the pricing app.
"""

from rest_framework import viewsets
from .models import PricePlan
from .serializers import PricePlanSerializer


class PricePlanViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows price plans to be viewed or edited.
    """

    queryset = PricePlan.objects.all()
    serializer_class = PricePlanSerializer

    def perform_create(self, serializer):
        """
        Custom method to create a new price plan and assign the requesting user as its creator.

        :param serializer: Serializer instance containing validated data
        """
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        """
        Custom method to update an existing price plan and record the last updated user.

        :param serializer: Serializer instance containing validated data
        """
        serializer.save(last_updated_by=self.request.user)