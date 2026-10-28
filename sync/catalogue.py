"""
Linking the m18 shop's catalogue to IQ products (``manage.py import_storefront_products``).

Each m18 product is, in this order: updated (already linked by ``Product.external_id``), excluded ("not sold
online"), skipped (no price or inactive), linked to one unlinked IQ product with the same barcode, linked to one
with the same name, or created. Several matches make it ``ambiguous`` and nothing is written for it. Every run
is saved as a ``CatalogueImport`` whose items staff review in the admin; a dry run saves only that report.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from inventory.models import Product, ProductCategory, StockBatch

from .models import CatalogueImport, CatalogueImportItem, StorefrontExclusion

Item = CatalogueImportItem


def barcodes_of(item):
    """An m18 product's barcodes: the main one, then the alternates; stripped, without blanks or repeats."""
    codes = [item.get('barcode'), *(item.get('alternate_barcodes') or [])]
    return list(dict.fromkeys(code.strip() for code in codes if isinstance(code, str) and code.strip()))


def _normal(name):
    return ' '.join(name.split()).casefold()


def _price(raw):
    try:
        return Decimal(str(raw['price'])).quantize(Decimal('0.01'))
    except (InvalidOperation, KeyError):
        return Decimal(0)


def summary(run):
    counts = run.counts
    linked = sum(counts.get(outcome, 0) for outcome in (Item.UPDATED, Item.LINKED_BARCODE, Item.LINKED_NAME, Item.CREATED))
    attention = sum(counts.get(outcome, 0) for outcome in Item.NEEDS_ATTENTION)
    return (f"m18 products: {sum(counts.values())} — linked {linked}, excluded {counts.get(Item.EXCLUDED, 0)}, "
            f"needs attention {attention}")


class _Unlinked:
    """The company's IQ products not yet linked to the shop, indexed by barcode and by name."""

    def __init__(self, company):
        self.by_barcode, self.by_name, self.claimed = {}, {}, set()
        for product in Product.objects.filter(company=company, external_id='').order_by('pk'):
            if product.barcode.strip():
                self.by_barcode.setdefault(product.barcode.strip(), []).append(product)
            self.by_name.setdefault(_normal(product.name), []).append(product)

    def matching(self, products):
        return list({p.pk: p for p in products if p.pk not in self.claimed}.values())

    def for_barcodes(self, codes):
        return self.matching(p for code in codes for p in self.by_barcode.get(code, []))

    def for_name(self, name):
        return self.matching(self.by_name.get(_normal(name), []))


@transaction.atomic
def import_catalogue(company, catalog, *, barcodes, expiry, dry_run=False, run_by=None, source='api', keep_prices=False):
    """
    Link or create an IQ product for every product in ``catalog`` (``{"products": [...], "categories": {id: name}}``,
    the shop's public product API) and return the saved ``CatalogueImport``.

    ``barcodes`` maps an m18 product id to ``barcodes_of(...)`` from the signed snapshot; products missing from it
    use the barcodes in their own catalogue entry (a saved file). ``keep_prices`` (the connection pushes IQ's prices
    to m18) stops updates overwriting ``Product.price``. Stock of existing products is never changed.
    """
    from inventory.management.commands.import_storefront_products import OPENING_BATCH, product_name

    run = CatalogueImport.objects.create(company=company, run_by=run_by, dry_run=dry_run, source=source)
    linked = {p.external_id: p for p in Product.objects.filter(company=company).exclude(external_id='')}
    excluded = set(StorefrontExclusion.objects.filter(company=company).values_list('m18_product_id', flat=True))
    unlinked, categories, items = _Unlinked(company), _Categories(catalog.get('categories', {}), dry_run), []

    for raw in catalog['products']:
        m18_id, name, price = str(raw['product_id']), product_name(raw), _price(raw)
        codes = barcodes[m18_id] if m18_id in barcodes else barcodes_of(raw)
        item = Item(run=run, m18_product_id=m18_id, name=name[:255], barcode=(codes[0] if codes else '')[:32],
                    m18_price=price if price > 0 else None)
        items.append(item)
        fields = _fields(raw, name, price, categories)
        product = linked.get(m18_id)

        if product is not None:
            item.outcome, item.product, item.iq_price = Item.UPDATED, product, product.price
            if keep_prices:
                fields.pop('price')
            if not dry_run:
                for key, value in fields.items():
                    setattr(product, key, value)
                product.save(update_fields=list(fields))
            continue
        if m18_id in excluded:
            item.outcome = Item.EXCLUDED
            continue
        if price <= 0 or not raw.get('is_active', True):
            item.outcome, item.detail = Item.SKIPPED, 'no price' if price <= 0 else 'inactive'
            continue

        for outcome, candidates in ((Item.LINKED_BARCODE, unlinked.for_barcodes(codes)), (Item.LINKED_NAME, unlinked.for_name(name))):
            if len(candidates) > 1:
                item.outcome, item.candidates = Item.AMBIGUOUS, [p.pk for p in candidates]
                item.detail = f"{len(candidates)} IQ products have this {'barcode' if outcome == Item.LINKED_BARCODE else 'name'}"
                break
            if candidates:
                product = candidates[0]
                unlinked.claimed.add(product.pk)
                item.outcome, item.product, item.iq_price = outcome, product, product.price
                if not dry_run:
                    link(product, m18_id, codes)
                break
        else:
            item.outcome = Item.CREATED
            if not dry_run:
                quantity = max(int(raw.get('stock_quantity') or 0), 0)
                item.product = Product.objects.create(company=company, external_id=m18_id, stock_quantity=quantity,
                                                      barcode=item.barcode, **fields)
                item.iq_price = item.product.price
                if quantity:
                    StockBatch.objects.create(product=item.product, batch_number=OPENING_BATCH, quantity=quantity,
                                              expiration_date=expiry)

    Item.objects.bulk_create(items)
    for item in items:
        run.counts[item.outcome] = run.counts.get(item.outcome, 0) + 1
    run.save(update_fields=['counts'])
    return run


def link(product, m18_id, codes=()):
    """Link an IQ product to an m18 product, taking the m18 barcode if the product has none."""
    product.external_id = m18_id
    update = ['external_id']
    if not product.barcode.strip() and codes:
        product.barcode = codes[0][:32]
        update.append('barcode')
    product.save(update_fields=update)


def _fields(raw, name, price, categories):
    """What the shop decides about a product it sells: name, description, price and category."""
    return {'name': name[:255], 'description': (raw.get('description') or '').strip(), 'price': price,
            'category': categories.get(raw.get('category'))}


class _Categories:
    """IQ categories by the shop's category id; created on first use (looked up only, in a dry run)."""

    def __init__(self, names, dry_run):
        self.names, self.dry_run, self.cache = names, dry_run, {}

    def get(self, category_id):
        name = self.names.get(str(category_id))
        if not name:
            return None
        if name not in self.cache:
            self.cache[name] = (ProductCategory.objects.filter(name=name).first() if self.dry_run
                                else ProductCategory.objects.get_or_create(name=name)[0])
        return self.cache[name]


def link_item(item, product, user):
    """Staff linked an import item to an IQ product by hand (admin)."""
    with transaction.atomic():
        link(product, item.m18_product_id, [item.barcode] if item.barcode else [])
        item.product, item.resolved_by, item.resolved_at = product, user, timezone.now()
        item.save(update_fields=['product', 'resolved_by', 'resolved_at'])


def exclude_item(item, user, reason=''):
    """Staff marked an import item "not sold online" (admin); the next import skips it. Idempotent."""
    with transaction.atomic():
        exclusion, _ = StorefrontExclusion.objects.get_or_create(
            company=item.run.company, m18_product_id=item.m18_product_id,
            defaults={'name': item.name, 'excluded_by': user, 'reason': reason},
        )
        if item.resolved_at is None:
            item.resolved_by, item.resolved_at = user, timezone.now()
            item.save(update_fields=['resolved_by', 'resolved_at'])
    return exclusion
