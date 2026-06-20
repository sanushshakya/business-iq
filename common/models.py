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
    - access_token: CharField representing the access token for authentication.
    """

    company = models.ForeignKey('authentication.Company', on_delete=models.CASCADE)
    shop_domain = models.CharField(max_length=255, validators=[URLValidator(schemes=['https'])])
    access_token = models.CharField(max_length=256)

    def __str__(self):
        return f"{self.company.name} - {self.shop_domain}"


class FreightRateCache(models.Model):
    """
    Model representing cached freight rates.

    Fields:
    - shop_connection: ForeignKey to the ShopifyConnection model, linking the rate data to a specific Shopify connection.
    - service_code: CharField representing the shipping service code.
    - rate: DecimalField representing the current rate for the service.
    - currency: CharField representing the currency in which the rate is denominated.
    - last_updated: DateTimeField automatically updated when the model instance is saved.
    """

    shop_connection = models.ForeignKey(ShopifyConnection, on_delete=models.CASCADE)
    service_code = models.CharField(max_length=100)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.shop_connection.shop_domain} - {self.service_code} - ${self.rate}"