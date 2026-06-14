# common/views.py

from rest_framework import generics
from .models import StockProjection
from .serializers import StockProjectionSerializer

class StockProjectionDetailView(generics.RetrieveAPIView):
    """
    API endpoint to retrieve stock projection data for a specific product.

    This endpoint allows clients to fetch the current and projected stock price,
    as well as the date of the projection, for a given product identified by its ID.
    
    URL parameters:
    - <product_id>: Integer representing the unique identifier of the product.

    Returns:
    - JSON object containing the stock projection data if found.
    - 404 Not Found if no stock projection exists for the specified product ID.
    """
    queryset = StockProjection.objects.all()
    serializer_class = StockProjectionSerializer
    lookup_field = 'product_id'

# End of file