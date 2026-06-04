"""
config/common/urls.py

URL routing for common application.

This file defines the URL patterns for the 'common' Django app, which likely contains reusable components and utilities across different parts of the project.
"""

from django.urls import path
from . import views  # Assuming views are located in config/common/views.py

urlpatterns = [
    # Define URL patterns here
    # Example:
    # path('some-endpoint/', views.some_view_function, name='some_view_name'),
]