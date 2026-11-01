# inventory/barcodes.py

"""G16: from a scanned GS1 element string to the company's products (the parsing itself is ``gs1_parser``)."""

from django.db.models.functions import Trim

from vat.services import uk_date

from .gs1_parser import parse_gs1
from .models import Product


def gtin_forms(gtin):
    """The ways a GTIN-14 may be stored as a pack barcode: itself, and the GTIN-13, UPC-12 or EAN-8 it pads."""
    if not gtin:
        return []
    return [gtin] + [gtin[zeros:] for zeros in (1, 2, 6) if gtin.startswith('0' * zeros)]


def products_for_gtin(company, gtin):
    """The company's products whose barcode is a form of ``gtin``; several can match (barcodes are not unique)."""
    forms = gtin_forms(gtin)
    return (Product.objects.filter(company=company).annotate(trimmed_barcode=Trim('barcode'))
            .filter(trimmed_barcode__in=forms).order_by('pk'))


def read_gs1(company, code, today=None):
    """Parse ``code`` (raises ``GS1Error``) and find its products; ``today`` defaults to the UK date."""
    parsed = parse_gs1(code, today or uk_date())
    return parsed, list(products_for_gtin(company, parsed.gtin))
