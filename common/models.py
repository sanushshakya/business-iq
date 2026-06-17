# common/models.py

from django.db import models
from django.core.validators import URLValidator
from django.utils.crypto import get_random_string, pbkdf2_hmac

class ShopifyConnection(models.Model):
    """
    Model representing a connection to a Shopify store.

    Fields:
    - company: ForeignKey to the Company model, linking the connection to a specific company.
    - shop_domain: CharField representing the domain of the Shopify store.
    - access_token_encrypted: TextField representing the encrypted access token for the store.
    - connected_at: DateTimeField representing the timestamp when the store was connected.
    - active: BooleanField indicating whether the connection is currently active.
    """
    company = models.ForeignKey('authentication.Company', on_delete=models.CASCADE)
    shop_domain = models.CharField(max_length=255, validators=[URLValidator()])
    access_token_encrypted = models.TextField()
    connected_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        """
        Override the default save method to encrypt the access token before saving.
        """
        if self.access_token:
            self.access_token_encrypted = self.encrypt_access_token(self.access_token)
        super(ShopifyConnection, self).save(*args, **kwargs)

    def encrypt_access_token(self, access_token):
        """
        Encrypt the access token using a secure hashing algorithm.

        Args:
        - access_token (str): The access token to be encrypted.

        Returns:
        - str: The encrypted access token.
        """
        salt = get_random_string(length=16)
        return pbkdf2_hmac('sha256', access_token.encode(), salt.encode(), 32).hex()

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
        fields = '__all__'

class PriceChangeLogSerializer(serializers.ModelSerializer):
    """
    Serializer for the PriceChangeLog model.

    This serializer is used to convert PriceChangeLog objects into JSON and vice versa.
    """

    class Meta:
        model = PriceChangeLog
        fields = '__all__'