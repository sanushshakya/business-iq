# sync/serializers.py

from rest_framework import serializers
from .models import SyncModel

class SyncSerializer(serializers.ModelSerializer):
    """
    Serializer for the SyncModel model using Django REST Framework.
    
    This serializer defines how instances of SyncModel are converted to and from JSON.
    """

    class Meta:
        """
        Meta class for specifying additional options for the serializer.
        
        Attributes:
            model (SyncModel): The model that this serializer is bound to.
            fields (list): A list of model fields to include in the serialized output.
        """
        model = SyncModel
        fields = '__all__'

    def validate_field_name(self, value):
        """
        Validate the 'field_name' field to ensure it meets specific criteria.
        
        Args:
            value (str): The value to be validated.
            
        Returns:
            str: The validated value.
            
        Raises:
            serializers.ValidationError: If the value does not meet the criteria.
        """
        if len(value) < 3:
            raise serializers.ValidationError("Field name must be at least 3 characters long.")
        return value