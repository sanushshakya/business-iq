"""
config/feature/views.py

This module contains the views for handling CRUD operations on the Product model.
"""

from rest_framework import viewsets, status
from rest_framework.response import Response
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

    def create(self, request, *args, **kwargs):
        """
        Create a new product with edge-case handling and validation.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            self.perform_create(serializer)
        except serializers.ValidationError as e:
            return Response({'error': 'SKU code must be unique across all companies.'}, status=status.HTTP_400_BAD_REQUEST)

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        """
        Update an existing product with edge-case handling and validation.
        """
        instance = self.get_object()
        
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        
        try:
            self.perform_update(serializer)
        except serializers.ValidationError as e:
            return Response({'error': 'SKU code must be unique across all companies.'}, status=status.HTTP_400_BAD_REQUEST)

        if getattr(instance, '_prefetched_objects_cache', None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            instance._prefetched_objects_cache = {}

        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        """
        Delete an existing product with edge-case handling.
        """
        instance = self.get_object()
        
        try:
            self.perform_destroy(instance)
        except Exception as e:
            return Response({'error': 'An error occurred while deleting the product.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(status=status.HTTP_204_NO_CONTENT)