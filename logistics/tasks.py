# logistics/tasks.py

import logging
from decimal import Decimal

import requests
from celery import shared_task
from django.conf import settings

from common.models import FreightRateCache

from tenants.models import Company

from .models import FreightAlert

logger = logging.getLogger(__name__)


@shared_task
def check_freight_rates():
    """
    Fetch current freight rates and raise a FreightAlert when a service's rate has moved by at least
    ``settings.RATE_CHANGE_THRESHOLD`` percent from its baseline.

    The API is expected to return a list of ``{"company_id", "shipping_lane", "current_rate"}`` (optionally
    with ``"currency"``). The baseline is the ``FreightRateCache`` row: it is recorded the first time a
    service is seen and replaced whenever an alert is raised, so a slow drift still triggers an alert once
    it adds up. Returns the number of alerts created.
    """
    if not settings.FREIGHT_RATES_API_URL:
        logger.warning("FREIGHT_RATES_API_URL is not configured; skipping freight rate check.")
        return 0

    try:
        response = requests.get(settings.FREIGHT_RATES_API_URL, timeout=settings.HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        rates = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Error fetching freight rates: %s", exc)
        return 0

    if not isinstance(rates, list):
        logger.error("Unexpected freight rates payload (%s); expected a list.", type(rates).__name__)
        return 0

    known_companies = set(Company.objects.values_list('pk', flat=True))
    created = 0
    for info in rates:
        try:
            company_id = int(info['company_id'])
            lane = str(info['shipping_lane'])
            current = Decimal(str(info['current_rate']))
            if not current.is_finite() or current < 0:
                raise ValueError('rate must be a non-negative number')
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            logger.warning("Skipping malformed freight rate %r: %s", info, exc)
            continue
        if company_id not in known_companies:
            logger.warning("Skipping freight rate for unknown company %s.", company_id)
            continue

        baseline, first_sighting = FreightRateCache.objects.get_or_create(
            company_id=company_id, service_code=lane,
            defaults={'rate': current, 'currency': info.get('currency', 'GBP')},
        )
        if first_sighting or not baseline.rate:
            continue  # nothing to compare against yet
        change_percent = (current - baseline.rate) / baseline.rate * 100
        if abs(change_percent) >= Decimal(str(settings.RATE_CHANGE_THRESHOLD)):
            FreightAlert.objects.create(
                company_id=company_id,
                shipping_lane=lane,
                current_rate=current,
                baseline_rate=baseline.rate,
                change_percent=float(change_percent),
            )
            baseline.rate = current
            baseline.currency = info.get('currency', baseline.currency)
            baseline.save()
            created += 1
    return created
