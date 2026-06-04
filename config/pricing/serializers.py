# serializers.py

from rest_framework import serializers
from .models import PricingPlan

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