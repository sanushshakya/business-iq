from rest_framework import generics
from .models import FreightAlert

class AlertList(generics.ListAPIView):
    """
    API view to retrieve a list of freight alerts.

    This endpoint provides a way to fetch all freight alerts for a specific company.
    It is accessible only by authenticated users with the appropriate permissions.
    """

    queryset = FreightAlert.objects.all()
    serializer_class = FreightAlertSerializer
    # TODO: Implement permission checks and filtering by company

class AlertDetail(generics.RetrieveAPIView):
    """
    API view to retrieve details of a single freight alert.

    This endpoint provides access to detailed information about a specific freight alert.
    It is accessible only by authenticated users with the appropriate permissions.
    """

    queryset = FreightAlert.objects.all()
    serializer_class = FreightAlertSerializer
    # TODO: Implement permission checks and filtering by company