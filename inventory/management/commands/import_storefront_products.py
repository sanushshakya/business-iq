import json
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from inventory.models import Product, ProductCategory, StockBatch
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
        "Import the online shop's products into a company. Safe to repeat: products are matched on the shop's id, "
        'so a re-run updates names, descriptions and prices but never stock. New products with stock get one '
        f'"{OPENING_BATCH}" batch so the till can sell them.'
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

        result = self.import_catalog(
            company, catalog, expiry=timezone.localdate() + timedelta(days=options['expiry_days']),
            dry_run=options['dry_run'],
        )
        self.stdout.write(self.style.SUCCESS(
            f"{'Would import' if options['dry_run'] else 'Imported'}: {result['created']} new, "
            f"{result['updated']} updated, {len(result['skipped'])} skipped."
        ))
        for name, reason in result['skipped']:
            self.stdout.write(f'  skipped {name}: {reason}')

    @transaction.atomic
    def import_catalog(self, company, catalog, expiry, dry_run=False):
        category_names = catalog.get('categories', {})
        categories, existing = {}, {p.external_id: p for p in Product.objects.filter(company=company).exclude(external_id='')}
        result = {'created': 0, 'updated': 0, 'skipped': []}

        for raw in catalog['products']:
            name = product_name(raw)
            external_id = str(raw['product_id'])
            try:
                price = Decimal(str(raw['price'])).quantize(Decimal('0.01'))
            except (InvalidOperation, KeyError):
                price = Decimal(0)
            if price <= 0:
                result['skipped'].append((name, 'no price'))
                continue
            if not raw.get('is_active', True):
                result['skipped'].append((name, 'inactive'))
                continue

            category = None
            category_name = category_names.get(str(raw.get('category')))
            if category_name:
                if category_name not in categories:
                    categories[category_name] = (
                        ProductCategory.objects.filter(name=category_name).first()
                        if dry_run else ProductCategory.objects.get_or_create(name=category_name)[0]
                    )
                category = categories[category_name]

            product = existing.get(external_id)
            fields = {'name': name[:255], 'description': (raw.get('description') or '').strip(), 'price': price, 'category': category}
            if product:
                result['updated'] += 1
                if not dry_run:
                    for key, value in fields.items():
                        setattr(product, key, value)
                    product.save(update_fields=list(fields))
                continue

            result['created'] += 1
            if dry_run:
                continue
            quantity = max(int(raw.get('stock_quantity') or 0), 0)
            product = Product.objects.create(company=company, external_id=external_id, stock_quantity=quantity, **fields)
            if quantity:
                StockBatch.objects.create(product=product, batch_number=OPENING_BATCH, quantity=quantity, expiration_date=expiry)
        return result
