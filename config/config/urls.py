"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    # Include inventory URLs
    path('inventory/', include('inventory.urls')),
    # Include demand URLs
    path('demand/', include('demand.urls')),
    # Include logistics URLs
    path('logistics/', include('logistics.urls')),
    # Include pricing URLs
    path('pricing/', include('pricing.urls')),  # Added for the new pricing app
    # Include sync URLs
    path('sync/', include('sync.urls')),  # Added for the new sync app
    # Include common URLs
    path('common/', include('common.urls')),  # Added for the new common app
]