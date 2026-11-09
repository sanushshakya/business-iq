"""Celery tasks of the wholesale app, imported by the worker's autodiscovery (``config/celery.py``)."""

from .invoices import send_invoice_email  # noqa: F401 - registers wholesale.invoices.send_invoice_email
from .credit_notes import send_credit_note_email  # noqa: F401,E402 - registers wholesale.credit_notes.send_credit_note_email
from .customer_emails import deliver_customer_email, sweep_customer_emails  # noqa: F401,E402 - G51
from .reminders import run_reminders  # noqa: F401,E402
from .statements import run_monthly_statements  # noqa: F401,E402
