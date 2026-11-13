# sales/catalogue.py

"""
The till catalogue (G26, ADR 0001): what a till keeps in its browser to find and price products when the server can't
be reached. Products are sent as changes since a cursor; the small lists (VAT rates, barcode rules, tills, branches,
settings) are always sent whole.

The cursor is the server time when the request started. A delta returns products and removals changed at or after
``since − OVERLAP``, so a change whose transaction was still committing when the last cursor was taken is sent again
rather than missed; the till applies rows by id, so repeats do no harm.
"""

from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from common.services.settings_service import get_company_setting
from inventory.models import BarcodeRule, Product, ProductRemoval
from promotions.services import catalogue_promotions
from tenants.models import Branch, Till
from vat.models import VatRate
from vat.services import uk_date

from .services import manual_weight_limit

OVERLAP = timedelta(seconds=60)
OFFLINE_HOURS = (('offline_warn_hours', 4), ('offline_confirm_hours', 24), ('offline_stop_hours', 72))


def read_cursor(text):
    """The instant a cursor names, or None (no cursor, or one that can't be read: the till gets everything)."""
    if not text:
        return None
    try:
        moment = parse_datetime(text)
    except ValueError:
        return None
    if moment is None or timezone.is_naive(moment):
        return None
    return moment


def _hours(company_id, key, default):
    hours = get_company_setting(company_id, key, default, int)
    return hours if hours > 0 else default  # a typo must not stop the till at once


def till_settings(company_id):
    return {
        'manual_weight_max_kg': manual_weight_limit(company_id),
        **{key: _hours(company_id, key, default) for key, default in OFFLINE_HOURS},
    }


def catalogue(user, since=None):
    """The till catalogue for ``user``'s company: everything (``since`` None) or what changed since that instant."""
    company = user.company
    cursor = timezone.now()
    products = Product.objects.filter(company=company).order_by('pk')
    removed = ProductRemoval.objects.filter(company=company)
    if since is not None:
        products = products.filter(updated_at__gte=since - OVERLAP)
        removed = removed.filter(removed_at__gte=since - OVERLAP)
    else:
        removed = removed.none()
    return {
        'cursor': cursor,
        'full': since is None,
        'user': user.pk,
        'company': company.pk,
        'products': products,
        'removed': sorted(set(removed.values_list('product_id', flat=True))),
        'vat_rates': VatRate.objects.order_by('code', 'valid_from'),
        'barcode_rules': BarcodeRule.objects.filter(company=company, is_active=True).order_by('prefix'),
        'tills': Till.objects.filter(branch__company=company, is_active=True).order_by('branch_id', 'number'),
        'branches': Branch.objects.filter(company=company).order_by('name'),
        'settings': till_settings(company.pk),
        'promotions': catalogue_promotions(company.pk, uk_date()),
    }
