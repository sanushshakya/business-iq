# models.py

from django.db import models

class BaseModel(models.Model):
    """
    An abstract base model with a UUIDField and timestamps.

    Attributes:
        id (UUID): The unique identifier for the object.
        created_at (datetime): The timestamp when the object was created.
        updated_at (datetime): The timestamp when the object was last updated.
    """

    id = models.UUIDField(primary_key=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True