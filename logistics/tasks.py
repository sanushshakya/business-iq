# logistics/tasks.py

import logging

import requests
from celery import shared_task
from django.conf import settings

from .models import FreightAlert

logger = logging.getLogger(__name__)


@shared_task
def check_freight_rates():
    """
    Fetch current freight rates and create a FreightAlert when a lane's rate has moved by
    at least ``settings.RATE_CHANGE_THRESHOLD`` percent against its last known rate.

    The API is expected to return a list of ``{"company_id", "shipping_lane", "current_rate"}``.
    Returns the number of alerts created.
    """
    if not settings.FREIGHT_RATES_API_URL:
        logger.warning("FREIGHT_RATES_API_URL is not configured; skipping freight rate check.")
        return 0

    try:
        response = requests.get(settings.FREIGHT_RATES_API_URL, timeout=30)
        response.raise_for_status()
        rates = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Error fetching freight rates: %s", exc)
        return 0

    created = 0
    for info in rates:
        current = info['current_rate']
        last = (
            FreightAlert.objects.filter(company_id=info['company_id'], shipping_lane=info['shipping_lane'])
            .order_by('-alert_date')
            .first()
        )
        if last is None or not last.current_rate:
            continue  # no baseline to compare against
        change_percent = float((current - float(last.current_rate)) / float(last.current_rate) * 100)
        if abs(change_percent) >= settings.RATE_CHANGE_THRESHOLD:
            FreightAlert.objects.create(
                company_id=info['company_id'],
                shipping_lane=info['shipping_lane'],
                current_rate=current,
                baseline_rate=last.current_rate,
                change_percent=change_percent,
            )
            created += 1
    return created
