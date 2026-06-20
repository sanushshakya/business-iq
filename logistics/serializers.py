# logistics/serializers.py

from rest_framework import serializers
from .models import UserSupplier, AlternativeSupplier

class UserSupplierSerializer(serializers.ModelSerializer):
    """
    Serializer for converting UserSupplier model instances to JSON and vice versa.
    
    Fields:
    - id: IntegerField representing the primary key of the user supplier.
    - name: CharField representing the name of the user supplier.
    - contact_email: EmailField representing the contact email of the user supplier.
    - phone_number: CharField representing the phone number of the user supplier.
    - address_line1: CharField representing the first line of the user supplier's address.
    - address_line2: CharField representing the second line of the user supplier's address (optional).
    - city: CharField representing the city of the user supplier's address.
    - state: CharField representing the state of the user supplier's address.
    - postal_code: CharField representing the postal code of the user supplier's address.
    - country: CharField representing the country of the user supplier's address.
    """

    class Meta:
        model = UserSupplier
        fields = '__all__'


class AlternativeSupplierSerializer(serializers.ModelSerializer):
    """
    Serializer for converting AlternativeSupplier model instances to JSON and vice versa.
    
    Fields:
    - id: IntegerField representing the primary key of the alternative supplier.
    - name: CharField representing the name of the alternative supplier.
    - contact_email: EmailField representing the contact email of the alternative supplier.
    - phone_number: CharField representing the phone number of the alternative supplier.
    - address_line1: CharField representing the first line of the alternative supplier's address.
    - address_line2: CharField representing the second line of the alternative supplier's address (optional).
    - city: CharField representing the city of the alternative supplier's address.
    - state: CharField representing the state of the alternative supplier's address.
    - postal_code: CharField representing the postal code of the alternative supplier's address.
    - country: CharField representing the country of the alternative supplier's address.
    """

    class Meta:
        model = AlternativeSupplier
        fields = '__all__'