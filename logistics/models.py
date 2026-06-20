from django.db import models
from django.core.validators import URLValidator
from django.utils.crypto import get_random_string, pbkdf2_hmac

class AlternativeSupplier(models.Model):
    """
    Model representing an alternative supplier for logistics.

    Fields:
    - name: CharField representing the name of the alternative supplier.
    - country: CharField representing the country where the supplier is located.
    - product_categories: JSONField representing a list of product categories that the supplier specializes in.
    - contact_url: URLField representing the URL to contact the supplier.
    """
    
    # Define validators
    url_validator = URLValidator()

    name = models.CharField(max_length=255, help_text="The name of the alternative supplier.")
    country = models.CharField(max_length=100, help_text="The country where the supplier is located.")
    product_categories = models.JSONField(help_text="A list of product categories that the supplier specializes in.")
    contact_url = models.URLField(validators=[url_validator], help_text="The URL to contact the supplier.")

    def __str__(self):
        """
        String representation of the AlternativeSupplier model.
        
        Returns:
            str: The name of the alternative supplier.
        """
        return self.name