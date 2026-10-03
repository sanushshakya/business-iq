# authentication/tasks.py

from celery import shared_task

from .refresh_tokens import prune


@shared_task
def prune_refresh_tokens():
    """Delete long-expired and long-revoked refresh tokens. Returns the number removed."""
    return prune()
