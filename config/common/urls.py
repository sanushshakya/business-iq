"""
config/common/urls.py

This file contains the URL configurations for common parts of the iq project,
which are not specific to any single app but rather shared across multiple apps.
"""

from django.urls import path
from . import views  # Assuming a basic view for demonstration purposes

urlpatterns = [
    path('', views.home, name='home'),  # Placeholder URL pattern
]