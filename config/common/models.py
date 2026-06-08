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

# Add TenantScopedModel here if it's not already defined in apps/common/models.py
class TenantScopedModel(models.Model):
    """
    Abstract base model for models that need to be scoped by tenant.
    
    Attributes:
        tenant (Tenant): A foreign key to the Tenant model, representing the tenant scope.
    """
    tenant = models.ForeignKey('apps.common.models.Tenant', on_delete=models.CASCADE)

    class Meta:
        abstract = True