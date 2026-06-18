from django.db import models
from django.core.validators import URLValidator
from django.utils.crypto import get_random_string, pbkdf2_hmac

class ShopifyConnection(models.Model):
    """
    Model representing a connection to a Shopify store.

    Fields:
    - company: ForeignKey to the Company model, linking the connection to a specific company.
    - shop_domain: CharField representing the domain of the Shopify store.
    - access_token: CharField representing the access token for authenticating requests to the Shopify API.
    """
    company = models.ForeignKey('authentication.Company', on_delete=models.CASCADE)
    shop_domain = models.CharField(max_length=255, validators=[URLValidator(schemes=['https'])])
    access_token = models.CharField(max_length=128)

    def __str__(self):
        return self.shop_domain

# authentication/models.py

from django.db import models
from common.models import ShopifyConnection

class Company(models.Model):
    """
    Model representing a company.

    Fields:
    - name: CharField representing the name of the company.
    - approval_mode: CharField representing the approval mode for pricing changes, choices are 'auto_apply' and 'require_approval', default is 'require_approval'.
    """

    AUTO_APPLY = 'auto_apply'
    REQUIRE_APPROVAL = 'require_approval'

    APPROVAL_MODE_CHOICES = [
        (AUTO_APPLY, 'Auto Apply'),
        (REQUIRE_APPROVAL, 'Require Approval'),
    ]

    name = models.CharField(max_length=100)
    approval_mode = models.CharField(
        max_length=20,
        choices=APPROVAL_MODE_CHOICES,
        default=REQUIRE_APPROVAL,
    )

    def save(self, *args, **kwargs):
        """
        Override the save method to trigger ShopifyService.update_product_price() when approval_mode is 'auto_apply' and a new PriceChangeLog is created.
        """
        # Call the parent class's save method
        super().save(*args, **kwargs)

        # Check if approval_mode is 'auto_apply'
        if self.approval_mode == self.AUTO_APPLY:
            from common.services import ShopifyService
            from .models import PriceChangeLog

            # Create a new PriceChangeLog instance (assuming this logic exists elsewhere)
            price_change_log = PriceChangeLog.objects.create(company=self, ...)

            # Trigger ShopifyService.update_product_price()
            ShopifyService.update_product_price(self, price_change_log)

# authentication/views.py
from django.shortcuts import render, redirect
from django.views.generic.edit import CreateView
from django.contrib.auth.models import User
from .models import Company, Owner
from common.models import ShopifyConnection

class RegisterView(CreateView):
    """
    View for user registration that creates a Company + Owner user in one transaction.
    """
    template_name = 'authentication/register.html'
    success_url = '/login/'

    def form_valid(self, form):
        """
        Handle the form validation logic.
        """
        # Your existing form validation logic here
        pass

# common/services.py
from django.conf import settings
import requests

class ShopifyService:
    """
    Service class for interacting with the Shopify API.
    """
    @staticmethod
    def update_product_price(company, price_change_log):
        """
        Update product prices in Shopify based on the given Company and PriceChangeLog.

        Args:
        - company: The Company instance associated with the price change log.
        - price_change_log: The PriceChangeLog instance containing details about the price change.
        """
        shop_connection = ShopifyConnection.objects.get(company=company)
        api_url = f'https://{shop_connection.shop_domain}/admin/api/2021-07/prices.json'
        headers = {
            'Content-Type': 'application/json',
            'X-Shopify-Access-Token': shop_connection.access_token,
        }
        payload = {
            # Your logic to prepare the payload for Shopify API request
            ...
        }

        response = requests.post(api_url, headers=headers, json=payload)
        if response.status_code != 201:
            raise Exception(f"Failed to update product price: {response.text}")

# common/models.py
from django.db import models

class PriceChangeLog(models.Model):
    """
    Model representing a log of changes in product prices.

    Fields:
    - company: ForeignKey to the Company model, linking the log to a specific company.
    - product_id: CharField representing the ID of the product being price-changed.
    - old_price: DecimalField representing the previous price of the product.
    - new_price: DecimalField representing the new price of the product.
    - change_date: DateTimeField representing the date and time when the price change occurred.
    """
    company = models.ForeignKey('authentication.Company', on_delete=models.CASCADE)
    product_id = models.CharField(max_length=100)
    old_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
    change_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Price change for product {self.product_id} by {self.company.name}"