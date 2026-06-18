# common/serializers.py

from rest_framework import serializers
from .models import Product, PriceChangeLog

class ProductSerializer(serializers.ModelSerializer):
    """
    Serializer for the Product model.

    This serializer is used to convert Product objects into JSON and vice versa.
    """

    class Meta:
        model = Product
        fields = ['id', 'name', 'description', 'price', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']

class PriceChangeLogSerializer(serializers.ModelSerializer):
    """
    Serializer for the PriceChangeLog model.

    This serializer is used to convert PriceChangeLog objects into JSON and vice versa.
    """

    class Meta:
        model = PriceChangeLog
        fields = ['id', 'product', 'old_price', 'new_price', 'change_date', 'status']
        read_only_fields = ['id', 'change_date']

    def validate_new_price(self, value):
        """
        Validate that the new price is greater than the old price.
        """

        product_id = self.initial_data.get('product')
        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            raise serializers.ValidationError("Product does not exist")

        if value <= product.price:
            raise serializers.ValidationError("New price must be greater than the old price")
        
        return value

    def create(self, validated_data):
        """
        Create a new PriceChangeLog instance and set the default status based on the company's approval_mode.
        """

        company_id = self.context['request'].data.get('product__company')
        try:
            company = Company.objects.get(id=company_id)
        except Company.DoesNotExist:
            raise serializers.ValidationError("Company does not exist")

        price_change_log = PriceChangeLog.objects.create(**validated_data)
        if company.approval_mode == 'auto_apply':
            price_change_log.status = PriceChangeLog.APPROVED
        else:
            price_change_log.status = PriceChangeLog.PENDING

        return price_change_log