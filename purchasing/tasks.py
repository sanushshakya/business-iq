"""Celery tasks of the purchasing app, imported by the worker's autodiscovery (``config/celery.py``)."""

from .drafting import run_nightly  # noqa: F401 - registers purchasing.drafting.run_nightly
