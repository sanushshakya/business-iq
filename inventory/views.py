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

    def receive_stock(self, request, pk=None):
        """
        Endpoint to receive stock for a specific inventory item.

        :param request: Request object
        :param pk: Primary key of the inventory item to receive stock
        :return: Response with updated stock level or error message
        """
        try:
            item = InventoryItem.objects.get(pk=pk)
            quantity_received = int(request.data.get('quantity', 0))
            item.stock_level += quantity_received
            item.save()
            return Response({'stock_level': item.stock_level}, status=status.HTTP_200_OK)
        except InventoryItem.DoesNotExist:
            return Response({'error': 'Inventory item not found'}, status=status.HTTP_404_NOT_FOUND)

    def decrement_stock(self, request, pk=None):
        """
        Endpoint to decrement stock for a specific inventory item.

        :param request: Request object
        :param pk: Primary key of the inventory item to decrement stock
        :return: Response with updated stock level or error message
        """
        try:
            item = InventoryItem.objects.get(pk=pk)
            quantity_to_decrement = int(request.data.get('quantity', 0))
            if item.stock_level >= quantity_to_decrement:
                item.stock_level -= quantity_to_decrement
                item.save()
                return Response({'stock_level': item.stock_level}, status=status.HTTP_200_OK)
            else:
                return Response({'error': 'Insufficient stock'}, status=status.HTTP_400_BAD_REQUEST)
        except InventoryItem.DoesNotExist:
            return Response({'error': 'Inventory item not found'}, status=status.HTTP_404_NOT_FOUND)

    def get_stock_level(self, request, pk=None):
        """
        Endpoint to get the stock level for a specific inventory item.

        :param request: Request object
        :param pk: Primary key of the inventory item to get stock level
        :return: Response with current stock level or error message
        """
        try:
            item = InventoryItem.objects.get(pk=pk)
            return Response({'stock_level': item.stock_level}, status=status.HTTP_200_OK)
        except InventoryItem.DoesNotExist:
            return Response({'error': 'Inventory item not found'}, status=status.HTTP_404_NOT_FOUND)

# Add these methods to the class and update your urls.py accordingly