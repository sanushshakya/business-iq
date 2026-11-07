"""Celery tasks of the wholesale app, imported by the worker's autodiscovery (``config/celery.py``)."""

from .invoices import send_invoice_email  # noqa: F401 - registers wholesale.invoices.send_invoice_email
