# common/views.py

from django.http import JsonResponse
from rest_framework.decorators import api_view
from .models import PriceChangeLog

@api_view(['GET'])
def price_change_log(request):
    """
    API endpoint to retrieve the list of price change logs.

    This function retrieves all records from the PriceChangeLog model and returns them as JSON.
    
    Returns:
        JsonResponse: A JSON response containing the list of price change logs.
    """
    try:
        # Retrieve all price change logs
        logs = PriceChangeLog.objects.all()
        
        # Convert queryset to list of dictionaries
        log_list = [{'id': log.id, 'product_name': log.product.name, 'old_price': log.old_price, 
                      'new_price': log.new_price, 'change_date': log.change_date} for log in logs]
        
        # Return JSON response
        return JsonResponse(log_list, safe=False)
    
    except PriceChangeLog.DoesNotExist:
        # Handle the case where no price change logs exist
        return JsonResponse({'error': 'No price change logs found'}, status=404)

# Additional views can be added here as needed for other functionalities.