# config/common/models.py

from django.db import models
from apps.common.models.base_model import BaseModel
from apps.company.models import Company

class TenantScopedModel(BaseModel):
    """
    Abstract model that extends BaseModel and includes a foreign key to the Company model.
    
    Attributes:
        company (ForeignKey): A foreign key linking the model instance to a specific Company.
    """
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='%(app_label)s_%(class)s_items',
        help_text="The company this item belongs to."
    )

    class Meta:
        abstract = True