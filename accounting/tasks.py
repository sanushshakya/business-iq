"""Celery tasks of the accounting app, imported by the worker's autodiscovery (``config/celery.py``)."""

from .postings import run_nightly  # noqa: F401 - registers accounting.postings.run_nightly
