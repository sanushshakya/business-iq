# config/sync/models.py

"""
Module for Django models in the sync app of the iq project.
"""

from django.db import models

class SyncTask(models.Model):
    """
    Model representing a synchronization task.
    
    Attributes:
        task_id (str): Unique identifier for the task.
        source_system (str): Name of the source system.
        target_system (str): Name of the target system.
        status (str): Current status of the task (e.g., 'pending', 'running', 'completed').
        start_time (datetime): Timestamp when the task started.
        end_time (datetime): Timestamp when the task ended.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    task_id = models.CharField(max_length=100, unique=True)
    source_system = models.CharField(max_length=100)
    target_system = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Task {self.task_id}: {self.status}"
```

This model represents a synchronization task within the sync app. It includes fields for the task ID, source and target systems, status of the task, and timestamps for when the task started and ended. The `STATUS_CHOICES` attribute ensures that the status is always one of the predefined choices.