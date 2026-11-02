# sales/labels.py

"""
G18: a scanned scale label (in-store EAN-13, G15) or GS1 label (G16) read on the server into the product, the kg and
the label price. The till reads labels too, to show the line, but only sends the raw code: this is what is charged.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from rest_framework import serializers

from inventory.barcode_parser import BarcodeError, parse
from inventory.barcodes import products_for_gtin
from inventory.gs1_parser import GS1Error, parse_gs1
from inventory.models import BarcodeRule, Product

KG, PENNY = Decimal('0.001'), Decimal('0.01')
MIN_KG, MAX_KG = Decimal('0.001'), Decimal('100')  # the same limits as a weighing typed at the till
GS1_PROBLEMS = {
    'format': 'damaged or incomplete', 'unknown_ai': 'a field we do not read', 'check_digit': 'check digit',
    'date': 'impossible date', 'value': 'price in fractions of a penny',
}


@dataclass(frozen=True)
class Label:
    product: Product
    weight_kg: Decimal | None  # 3 dp; for a weighed product always set (worked out from the price if need be)
    label_price: Decimal | None  # GBP printed on the label, charged as it is
    weight_from_price: bool  # kg = label price ÷ £/kg, so there is no independent price to compare
    raw: str


def refuse(message):
    raise serializers.ValidationError(message)


def read_label(company, code, today):
    """The ``Label`` for a scanned ``code``; raises ``ValidationError`` with a message a cashier can act on."""
    stripped = code.strip()
    if len(stripped) == 13 and stripped.isascii() and stripped.isdigit():
        product, weight, price = _scale_label(company, stripped)
    else:
        product, weight, price = _gs1_label(company, code, today)
    if weight is not None:
        weight = weight.quantize(KG, rounding=ROUND_HALF_UP)
    if price is not None and price != price.quantize(PENNY):  # a 3-decimal price rule; never charge part of a penny
        refuse('The label price is in fractions of a penny. Check the label rule, or ring it up.')
    if not product.is_weighed:
        if weight is not None:
            refuse(f'{product.name} is sold by each, but this is a weight label. Ring it up as a product.')
        return Label(product, None, None if price is None else price.quantize(PENNY), False, code)
    if weight is None and price is None:
        refuse(f'{product.name} is sold by weight, but this label has no weight or price. Weigh it.')
    from_price = weight is None
    if from_price:
        if not product.price_per_kg:
            refuse(f'{product.name}: no price per kg is set. Ask an admin.')
        if price <= 0:
            refuse(f'{product.name}: the label price is £0.00. Weigh it instead.')
        weight = (price / product.price_per_kg).quantize(KG, rounding=ROUND_HALF_UP)
    if not MIN_KG <= weight <= MAX_KG:
        refuse(f'{product.name}: the label weight {weight} kg is not between 0.001 and 100 kg. Weigh it instead.')
    return Label(product, weight, None if price is None else price.quantize(PENNY), from_price, code)


def _scale_label(company, code):
    rules = [rule.to_rule() for rule in BarcodeRule.objects.filter(company=company, is_active=True)]
    try:
        parsed = parse(code, rules)
    except BarcodeError as error:
        if error.reason == 'unknown_prefix':
            refuse('Not a price or weight label. Ring it up as a product.')
        refuse('The label did not read correctly (check digit). Scan it again or ring it up.')
    plu = parsed.item_code.lstrip('0')
    product = Product.objects.filter(company=company, plu=plu).first() if plu else None
    if product is None:
        refuse(f'No product has PLU {plu or parsed.item_code}. Set it on the product in Inventory.')
    return product, parsed.weight_kg, parsed.price


def _gs1_label(company, code, today):
    try:
        parsed = parse_gs1(code, today)
    except GS1Error as error:
        refuse(f'The label did not read correctly ({GS1_PROBLEMS[error.reason]}). Scan it again or ring it up.')
    if parsed.expired:
        refuse(f'Use-by date {parsed.expiry:%d/%m/%Y} has passed. Do not sell this.')
    if not parsed.gtin:
        refuse('This label has no product number (GTIN). Ring it up as a product.')
    products = list(products_for_gtin(company, parsed.gtin))
    if not products:
        refuse(f'No product has GTIN {parsed.gtin}. Set its barcode in Inventory.')
    if len(products) > 1:
        refuse(f'Several products share GTIN {parsed.gtin}. Fix their barcodes in Inventory.')
    return products[0], parsed.weight_kg, parsed.price
