# common/views.py

from rest_framework import generics
from .models import DemandAlert
from .serializers import DemandAlertSerializer


class DemandAlertList(generics.ListAPIView):
    """
    API view to retrieve demand alerts.

    This view provides a GET endpoint that returns a list of demand alerts, filtered by branch and dismissed status.
    """

    serializer_class = DemandAlertSerializer

    def get_queryset(self):
        """
        Filter the queryset based on query parameters 'branch' and 'dismissed'.

        :return: Queryset containing filtered demand alerts.
        """
        branch = self.request.query_params.get('branch', None)
        dismissed = self.request.query_params.get('dismissed', None)

        queryset = DemandAlert.objects.all()

        if branch is not None:
            queryset = queryset.filter(branch=branch)

        if dismissed is not None:
            queryset = queryset.filter(dismissed=bool(dismissed))

        return queryset