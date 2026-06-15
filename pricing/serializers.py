# serializers.py

from rest_framework import serializers
from .models import PricingPlan, SupplierInvoice

class PricingPlanSerializer(serializers.ModelSerializer):
    """
    Serializer for PricingPlan model.
    
    This serializer is used to serialize and deserialize PricingPlan objects
    in the RESTful API. It includes all fields from the PricingPlan model.
    """
    
    class Meta:
        """
        Meta class for PricingPlanSerializer.
        
        Defines the model and fields to be serialized.
        """
        model = PricingPlan
        fields = '__all__'

class SupplierInvoiceSerializer(serializers.ModelSerializer):
    """
    Serializer for SupplierInvoice model.
    
    This serializer is used to serialize and deserialize SupplierInvoice objects
    in the RESTful API. It includes all fields from the SupplierInvoice model.
    """
    
    class Meta:
        """
        Meta class for SupplierInvoiceSerializer.
        
        Defines the model and fields to be serialized.
        """
        model = SupplierInvoice
        fields = '__all__'