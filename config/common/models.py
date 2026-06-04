# config/common/models.py

from django.db import models

class BaseModel(models.Model):
    """
    Abstract base model for common fields and behaviors.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

class Setting(models.Model):
    """
    Model to store application settings.
    """

    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.key