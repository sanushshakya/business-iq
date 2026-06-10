"""
config/feature/models.py

This module contains the Product model for the inventory app.
"""

from django.db import models
from common.models import TenantModel

class Product(TenantModel):
    """
    Model representing a product in the inventory with fields for company foreign key, SKU code,
    name, category choices, unit choices, reorder threshold, target margin percent, perishability status,
    and shelf life days.
    """

    CATEGORY_CHOICES = [
        ('grains', 'Grains'),
        ('dairy', 'Dairy'),
        ('produce', 'Produce'),
        ('spices', 'Spices'),
        ('beverages', 'Beverages'),
        ('other', 'Other')
    ]

    UNIT_CHOICES = [
        ('kg', 'Kilogram'),
        ('g', 'Gram'),
        ('litre', 'Liter'),
        ('unit', 'Unit')
    ]

    company = models.ForeignKey('authentication.Company', on_delete=models.CASCADE)
    sku_code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES)
    reorder_threshold = models.IntegerField()
    target_margin_percent = models.DecimalField(max_digits=5, decimal_places=2)
    is_perishable = models.BooleanField(default=False)
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True)


"""
config/feature/views.py

This module contains the views for handling CRUD operations on the Product model.
"""

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import Product
from .serializers import ProductSerializer
from common.views import TenantModelViewSet

class ProductViewSet(TenantModelViewSet):
    """
    ViewSet for managing products with CRUD operations. Inherits from TenantModelViewSet to ensure tenant isolation.
    """

    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Filter the queryset by category and search by name/sku.
        """
        queryset = super().get_queryset()
        category = self.request.query_params.get('category')
        search_query = self.request.query_params.get('search')

        if category:
            queryset = queryset.filter(category=category)
        if search_query:
            queryset = queryset.filter(name__icontains=search_query) | queryset.filter(sku_code__icontains=search_query)

        return queryset


"""
config/feature/serializers.py

This module contains the serializers for the Product model.
"""

from rest_framework import serializers
from .models import Product

class ProductSerializer(serializers.ModelSerializer):
    """
    Serializer for the Product model to handle JSON serialization/deserialization.
    """

    class Meta:
        model = Product
        fields = '__all__'
```

This solution provides a complete implementation of the Product model with CRUD operations and filtering/search capabilities. The views are designed to work with Django REST Framework, ensuring proper authorization and tenant isolation through inheritance from `TenantModelViewSet`.