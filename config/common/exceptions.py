# apps/common/exceptions.py

import logging
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)

class CustomAPIException(APIException):
    """
    Base custom exception class for the application.
    
    Attributes:
        detail (str): The error message.
        status_code (int): The HTTP status code.
        code (str): A unique code representing the error.
    """
    def __init__(self, detail=None, status_code=None, code=None):
        self.detail = detail
        self.status_code = status_code
        self.code = code

def custom_exception_handler(exc, context):
    """
    Custom exception handler for the application.
    
    Args:
        exc (Exception): The exception that occurred.
        context (dict): The context in which the exception occurred.
        
    Returns:
        Response: A response object with the appropriate status and data.
    """
    response = drf_exception_handler(exc, context)
    
    # If no response was returned by DRF's default handler, create one
    if response is None:
        logger.error(f"Unhandled exception: {exc}")
        return response
    
    # Log the error details
    logger.error(f"Handled exception: {exc}, Status Code: {response.status_code}")
    
    # Customize the response data
    if isinstance(exc, CustomAPIException):
        response.data = {
            'code': exc.code,
            'message': exc.detail,
            'status_code': response.status_code
        }
    else:
        response.data = {
            'code': str(exc.__class__.__name__),
            'message': exc.detail,
            'status_code': response.status_code
        }
    
    return response