import json
import time
from datetime import timedelta

import requests
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from sync.catalogue import import_catalogue, summary
from sync.models import CatalogueImportItem as Item
from tenants.models import Company

DEFAULT_API = 'https://backend.m18foods.co.uk/shop/api/'
OPENING_BATCH = 'OPENING'
PAGE_SIZE = 100


def _get(url, tries=5):
    """GET a JSON document, backing off when the shop drops the connection (it rate limits)."""
    for attempt in range(tries):
        try:
            response = requests.get(url, timeout=60, headers={'User-Agent': 'Mozilla/5.0'})
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            if attempt == tries - 1:
                raise
            time.sleep(3 * (attempt + 1))


def fetch_catalog(api_url, pause=1.5):
    """Download every product and the category names from the shop's public API."""
    api_url = api_url.rstrip('/') + '/'
    products, url = [], f'{api_url}products/?limit={PAGE_SIZE}&offset=0'
    while url:
        page = _get(url)
        products.extend(page['results'])
        url = page['next']
        if url:
            time.sleep(pause)
    categories = _get(f'{api_url}categories/')
    categories = categories['results'] if isinstance(categories, dict) else categories
    return {'products': products, 'categories': {str(c['id']): c['name'] for c in categories}}


def product_name(raw):
    """The shop lists pack size separately; add it when the name has no size of its own ("Ghee" -> "Ghee 500g")."""
    name = ' '.join(raw['name'].split())
    size = (raw.get('net_contents') or '').strip()
    return f'{name} {size}' if size and not any(ch.isdigit() for ch in name) else name


class Command(BaseCommand):
    help = (
        "Link the online shop's products to a company's IQ products (sync.catalogue): by the shop's id, then barcode, "
        'then exact name; anything unmatched is created, with one '
        f'"{OPENING_BATCH}" batch if it has stock. Safe to repeat; stock of existing products is never changed. '
        'Every run is saved as a catalogue import report (admin).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--company', default='M18-DEMO', help='Registration number of the company (default M18-DEMO).')
        parser.add_argument('--api-url', default=DEFAULT_API)
        parser.add_argument('--file', help='Read a saved catalogue ({"products": [...], "categories": {"id": "name"}}) instead of the API.')
        parser.add_argument(
            '--expiry-days', type=int, default=365,
            help='The shop does not publish expiry dates; opening batches expire this many days from today (default 365).',
        )
        parser.add_argument('--dry-run', action='store_true', help='Report what would change and write nothing.')

    def handle(self, *args, **options):
        try:
            company = Company.objects.get(registration_number=options['company'])
        except Company.DoesNotExist:
            raise CommandError(f"No company with registration number {options['company']!r}.")

        if options['file']:
            with open(options['file']) as fh:
                catalog = json.load(fh)
        else:
            self.stdout.write(f"Downloading products from {options['api_url']} ...")
            catalog = fetch_catalog(options['api_url'])

        run = import_catalogue(
            company, catalog, barcodes={}, expiry=timezone.localdate() + timedelta(days=options['expiry_days']),
            dry_run=options['dry_run'], source='file' if options['file'] else 'api',
        )
        self.report(run)

    def report(self, run):
        counts, items = run.counts, run.items.all()
        linked = sum(counts.get(outcome, 0) for outcome in (Item.UPDATED, Item.LINKED_BARCODE, Item.LINKED_NAME))
        self.stdout.write(self.style.SUCCESS(
            f"{'Would import' if run.dry_run else 'Imported'}: {counts.get(Item.CREATED, 0)} new, "
            f"{linked} updated, {counts.get(Item.SKIPPED, 0)} skipped."
        ))
        self.stdout.write(summary(run))
        for item in items:
            if item.outcome in Item.NEEDS_ATTENTION:
                self.stdout.write(f'  {item.outcome} {item.name}: {item.detail}')
