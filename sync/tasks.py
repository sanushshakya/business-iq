# sync/tasks.py

from celery import shared_task
from django.utils import timezone

from .models import SyncTask


@shared_task
def process_sync_task(task_id):
    """
    Mark a pending SyncTask as running and then completed (or failed). A task that already ran is left alone.

    The actual transfer between systems is not implemented yet; this only maintains task state.

    Returns True if the task finished successfully, False otherwise.
    """
    try:
        task = SyncTask.objects.get(task_id=task_id)
    except SyncTask.DoesNotExist:
        return False

    if not SyncTask.objects.filter(pk=task.pk, status='pending').update(status='running'):
        return False  # already picked up by another worker, or finished

    task.status = 'running'
    task.start_time = timezone.now()
    task.save(update_fields=['status', 'start_time'])
    try:
        # Placeholder for the real synchronisation work.
        task.status = 'completed'
        return True
    except Exception:
        task.status = 'failed'
        return False
    finally:
        task.end_time = timezone.now()
        task.save(update_fields=['status', 'end_time'])
