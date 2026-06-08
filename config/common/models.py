# config/common/models.py

from django.db import models
from apps.common.models import TenantScopedModel, StoreBranch

class BranchScopedModel(TenantScopedModel):
    """
    Abstract model that extends TenantScopedModel and includes a foreign key to the StoreBranch model.
    
    Attributes:
        branch (StoreBranch): A foreign key to the StoreBranch model, representing the branch scope.
    """
    branch = models.ForeignKey('apps.common.models.StoreBranch', on_delete=models.CASCADE)

    class Meta:
        abstract = True