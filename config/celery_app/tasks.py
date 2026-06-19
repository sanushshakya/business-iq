# config/celery_app/tasks.py

from celery import shared_task
import time

@shared_task(idempotent=True)
def process_sync_event(event_id):
    """
    Celery task to process events idempotently.
    
    Args:
        event_id (int): The unique identifier for the event to process.

    Returns:
        bool: True if the event was processed successfully, False otherwise.
    """
    try:
        # Simulate event processing logic
        time.sleep(2)  # Sleep to mimic time-consuming operation
        print(f"Processing event {event_id}")
        return True
    except Exception as e:
        print(f"Failed to process event {event_id}: {e}")
        return False