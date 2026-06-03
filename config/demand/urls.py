# File: demand/urls.py

"""
URL routing for the 'demand' app in the 'iq' project.

This module defines the URL patterns for handling requests related to the demand management functionality.
"""

from django.urls import path
from . import views

urlpatterns = [
    # Define URL patterns here.
    path('list/', views.demand_list, name='demand-list'),
    path('create/', views.create_demand, name='create-demand'),
    path('<int:demand_id>/', views.demand_detail, name='demand-detail'),
    path('<int:demand_id>/update/', views.update_demand, name='update-demand'),
    path('<int:demand_id>/delete/', views.delete_demand, name='delete-demand'),
]

# Explanation:
# - The 'path' function is used to map URLs to view functions.
# - 'views.demand_list' corresponds to the URL pattern for listing all demands.
# - '<int:demand_id>' is a dynamic segment that captures an integer value and passes it as 'demand_id' to the corresponding view function.
# - The names ('demand-list', 'create-demand', etc.) are used in templates and reverse() functions to refer to these URLs.