# config/celery.py

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'scan-demand-alerts-weekly': {
        'task': 'common.tasks.scan_demand_alerts',
        'schedule': crontab(hour=6, minute=0, day_of_week=1),  # Mondays 06:00 UTC
    },
    'sync-islamic-events-weekly': {
        'task': 'demand_calendar.tasks.sync_islamic_events',
        'schedule': crontab(hour=5, minute=0, day_of_week=0),  # Sundays 05:00 UTC, before Monday's alert scan
    },
    'check-low-stock-hourly': {
        'task': 'common.tasks.check_low_stock',
        'schedule': crontab(minute=0),
    },
    'check-freight-rates-hourly': {
        'task': 'logistics.tasks.check_freight_rates',
        'schedule': crontab(minute=30),
    },
    'prune-refresh-tokens-daily': {
        'task': 'authentication.tasks.prune_refresh_tokens',
        'schedule': crontab(hour=3, minute=15),
    },
    'propose-decay-markdowns-daily': {
        'task': 'pricing.tasks.propose_decay_markdowns',
        'schedule': crontab(hour=2, minute=0),
    },
    'sync-approved-prices-every-15-minutes': {
        'task': 'pricing.tasks.sync_approved_prices',
        'schedule': crontab(minute='*/15'),
    },
    # Each event is sent the moment its sale commits; this only retries failures and anything a dead worker dropped.
    # Online stock for the storefront: changed products are sent in batches within ~15 s.
    'push-storefront-stock-every-15-seconds': {
        'task': 'sync.tasks.push_storefront_stock',
        'schedule': 15.0,
    },
    # After midnight, so stock that has just expired drops off the storefront too.
    'reconcile-storefront-stock-nightly': {
        'task': 'sync.tasks.reconcile_storefront_stock',
        'schedule': crontab(hour=0, minute=30),
    },
    'deliver-storefront-events-every-30-seconds': {
        'task': 'sync.tasks.deliver_outbound_events',
        'schedule': 30.0,
    },
}
