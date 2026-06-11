from rest_framework import generics
from .models import StockAlert
from .serializers import StockAlertSerializer

class ActiveStockAlertsView(generics.ListAPIView):
    """
    API view to list active stock alerts per branch.
    
    This view returns a list of active StockAlert records, filtered by the current date and time.
    It allows querying alerts for a specific branch by adding a 'branch' query parameter.
    """

    queryset = StockAlert.objects.filter(dismissed_at__isnull=True)
    serializer_class = StockAlertSerializer

    def get_queryset(self):
        """
        Optionally filter the list of stock alerts by branch ID.

        Returns:
            QuerySet: Filtered list of active StockAlert records.
        """
        qs = super().get_queryset()
        branch_id = self.request.query_params.get('branch', None)
        if branch_id is not None:
            qs = qs.filter(branch=branch_id)
        return qs