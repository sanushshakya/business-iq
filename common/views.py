# common/views.py

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .models import PriceChangeLog

@csrf_exempt
@require_http_methods(["PATCH"])
def approve_price_change(request, log_id):
    """
    API endpoint to approve a price change log.

    Args:
        request (HttpRequest): The HTTP request object.
        log_id (int): The ID of the PriceChangeLog to be approved.

    Returns:
        JsonResponse: A JSON response indicating success or failure.
    """
    try:
        log = PriceChangeLog.objects.get(id=log_id)
    except PriceChangeLog.DoesNotExist:
        return JsonResponse({'error': 'Price change log not found.'}, status=404)

    if log.approved:
        return JsonResponse({'error': 'Price change log is already approved.'}, status=400)

    log.approved = True
    log.save()

    return JsonResponse({'message': 'Price change log approved successfully.'}, status=200)


@csrf_exempt
@require_http_methods(["PATCH"])
def reject_price_change(request, log_id):
    """
    API endpoint to reject a price change log.

    Args:
        request (HttpRequest): The HTTP request object.
        log_id (int): The ID of the PriceChangeLog to be rejected.

    Returns:
        JsonResponse: A JSON response indicating success or failure.
    """
    try:
        log = PriceChangeLog.objects.get(id=log_id)
    except PriceChangeLog.DoesNotExist:
        return JsonResponse({'error': 'Price change log not found.'}, status=404)

    if log.approved:
        return JsonResponse({'error': 'Price change log is already approved.'}, status=400)

    log.approved = False
    log.save()

    return JsonResponse({'message': 'Price change log rejected successfully.'}, status=200)