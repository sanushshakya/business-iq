"""
config/pricing/urls.py

URL routing for the pricing app in the iq project.
"""

from django.urls import path
from . import views

urlpatterns = [
    # Define URL patterns here
    path('pricing/', views.pricing_list, name='pricing-list'),
    path('pricing/<int:id>/', views.pricing_detail, name='pricing-detail'),
]