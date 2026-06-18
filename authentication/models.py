from django.db import models

# Create your models here.


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