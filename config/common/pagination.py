# apps/common/pagination.py

from rest_framework.pagination import PageNumberPagination

class StandardResultsSetPagination(PageNumberPagination):
    """
    A standard pagination class for API responses.

    This class provides a consistent way to paginate API responses using the page number style.
    
    Attributes:
        page_size (int): The number of items per page.
        page_size_query_param (str): The query parameter used to specify the page size.
        max_page_size (int): The maximum number of items allowed per page.
    """
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100