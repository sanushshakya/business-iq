# views.py

from django.http import HttpResponse

def index(request):
    """
    A placeholder view that returns a simple HTTP response.

    Args:
        request (HttpRequest): The current HttpRequest instance.

    Returns:
        HttpResponse: A response with a message indicating the view is a placeholder.
    """
    return HttpResponse("This is a placeholder view.")