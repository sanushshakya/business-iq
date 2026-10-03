# common/exceptions.py

import logging

from rest_framework.exceptions import APIException, ValidationError
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class CustomAPIException(APIException):
    """
    Base class for application errors.

    Attributes:
        detail: the error message.
        status_code: HTTP status (defaults to 500).
        code: a stable, machine-readable error code.
    """

    def __init__(self, detail=None, status_code=None, code=None):
        super().__init__(detail=detail, code=code)
        if status_code is not None:
            self.status_code = status_code


def custom_exception_handler(exc, context):
    """
    DRF exception handler giving every non-validation error the same shape:
    ``{"code": ..., "message": ..., "status_code": ...}``.

    Validation errors keep DRF's default ``{"field": ["error"]}`` shape so clients can map
    messages onto form fields.
    """
    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled exception: %s", exc)
        return None

    if isinstance(exc, ValidationError):
        return response

    if isinstance(exc, APIException):
        code = getattr(exc.detail, 'code', None) or exc.default_code
        message = exc.detail
    else:  # Http404, PermissionDenied: DRF converted them but they carry no ``detail``
        code = exc.__class__.__name__
        message = response.data.get('detail', str(exc)) if isinstance(response.data, dict) else str(exc)

    response.data = {'code': code, 'message': message, 'status_code': response.status_code}
    return response
