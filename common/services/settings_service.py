# common/services/settings_service.py

import logging
from decimal import InvalidOperation

from common.models import Setting

logger = logging.getLogger(__name__)


def get_company_setting(company_id, key, default, cast=str):
    """The company's ``Setting`` value converted with ``cast`` (e.g. ``Decimal``, ``int``), else ``default``."""
    value = Setting.objects.filter(company_id=company_id, key=key).values_list('value', flat=True).first()
    if value is None:
        return default
    try:
        return cast(value.strip())
    except (ValueError, InvalidOperation):
        logger.warning("Setting %r for company %s is not a valid %s; using the default.", key, company_id, cast.__name__)
        return default
