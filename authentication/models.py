from django.db import models
from django.core.validators import URLValidator

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


class User(models.Model):
    """
    Model representing a user.

    Fields:
    - username: CharField representing the username of the user.
    - email: EmailField representing the email address of the user.
    - password: CharField representing the hashed password of the user.
    - is_verified: BooleanField representing whether the user's email has been verified, default is False.
    """

    username = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=256)
    is_verified = models.BooleanField(default=False)

    def __str__(self):
        return self.username


def send_verification_email(user: User):
    """
    Send a verification email to the user's email address.

    Args:
    - user (User): The user instance to send the verification email to.
    """

    # Logic to send an email with a verification link
    # This could involve generating a token, creating a URL, and sending it via SMTP or another method
    pass