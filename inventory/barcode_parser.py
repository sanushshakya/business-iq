# inventory/barcode_parser.py

"""
UK variable-measure EAN-13 (scale labels): a code starting with ``2`` is an in-store number carrying an item code and
a price or a weight. Each company describes its scales' layout as rules (``inventory.BarcodeRule``); this module
reads a code against them. Pure Python, no Django: the till's JavaScript parser (G17) mirrors it, and both are tested
against ``barcode_fixtures.json``.

Layout (13 digits): ``prefix | item code | [value check digit] | value | EAN-13 check digit``. The value takes the
digits left over. Check digits follow GS1 General Specifications 7.9 (EAN-13 mod 10; the price/weight verifier for
4- and 5-digit values).
"""

from dataclasses import dataclass
from decimal import Decimal

PRICE, WEIGHT = 'price', 'weight'

# GS1 weighted products per digit (General Specifications 7.9.3): "2-", "3", "5+" and "5-".
_TWO_MINUS = (0, 2, 4, 6, 8, 9, 1, 3, 5, 7)
_THREE = (0, 3, 6, 9, 2, 5, 8, 1, 4, 7)
_FIVE_PLUS = (0, 5, 1, 6, 2, 7, 3, 8, 4, 9)
_FIVE_MINUS = (0, 5, 9, 4, 8, 3, 7, 2, 6, 1)


class BarcodeError(ValueError):
    """``reason``: ``format``, ``check_digit``, ``unknown_prefix`` (an ordinary barcode) or ``value_check_digit``."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Rule:
    prefix: str
    item_code_length: int
    value_type: str
    decimals: int
    value_check_digit: bool
    is_active: bool = True

    @classmethod
    def from_dict(cls, data):
        return cls(
            prefix=str(data['prefix']), item_code_length=int(data['item_code_length']),
            value_type=data['value_type'], decimals=int(data['decimals']),
            value_check_digit=bool(data['value_check_digit']), is_active=bool(data.get('is_active', True)),
        )

    @property
    def value_length(self):
        return 12 - len(self.prefix) - self.item_code_length - (1 if self.value_check_digit else 0)

    def layout_error(self):
        """Why this layout can't describe a 13-digit code (``"<field>: message"``), or None."""
        if not (self.prefix.isascii() and self.prefix.isdigit() and self.prefix.startswith('2')
                and 1 <= len(self.prefix) <= 3):
            return 'prefix: 1 to 3 digits starting with 2 (the in-store range).'
        if self.item_code_length < 1:
            return 'item_code_length: at least 1 digit.'
        if not 4 <= self.value_length <= 6:
            return (f'item_code_length: the value would be {self.value_length} digits; '
                    'a 13-digit code leaves room for 4 to 6.')
        if self.value_check_digit and self.value_length not in (4, 5):
            return f'value_check_digit: GS1 defines it for 4- or 5-digit values, not {self.value_length}.'
        if self.value_type not in (PRICE, WEIGHT):
            return 'value_type: price or weight.'
        if not 0 <= self.decimals <= 3:
            return 'decimals: 0 to 3.'
        return None


@dataclass(frozen=True)
class Parsed:
    rule: Rule
    item_code: str
    value: Decimal  # price in GBP or weight in kg, with the rule's decimals
    price: Decimal | None
    weight_kg: Decimal | None


def ean13_check_digit(first12):
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(first12))
    return (10 - total % 10) % 10


def value_check_digit(digits):
    """GS1 price/weight verifier digit for a 4- or 5-digit value."""
    d = [int(digit) for digit in digits]
    if len(d) == 4:
        return (_TWO_MINUS[d[0]] + _TWO_MINUS[d[1]] + _THREE[d[2]] + _FIVE_MINUS[d[3]]) * 3 % 10
    if len(d) == 5:
        total = _FIVE_PLUS[d[0]] + _TWO_MINUS[d[1]] + _FIVE_MINUS[d[2]] + _FIVE_PLUS[d[3]] + _TWO_MINUS[d[4]]
        return _FIVE_MINUS.index((10 - total % 10) % 10)
    raise ValueError('A value check digit covers 4 or 5 digits.')


def parse(code, rules):
    """Read a scanned code with the longest-prefix active, well-formed rule; raises ``BarcodeError``."""
    code = code.strip()
    if len(code) != 13 or not (code.isascii() and code.isdigit()):
        raise BarcodeError('format')
    if ean13_check_digit(code[:12]) != int(code[12]):
        raise BarcodeError('check_digit')
    matches = [rule for rule in rules
               if rule.is_active and rule.layout_error() is None and code.startswith(rule.prefix)]
    if not matches:
        raise BarcodeError('unknown_prefix')
    rule = max(matches, key=lambda rule: len(rule.prefix))
    start = len(rule.prefix) + rule.item_code_length
    item_code = code[len(rule.prefix):start]
    if rule.value_check_digit:
        check, start = int(code[start]), start + 1
    digits = code[start:12]
    if rule.value_check_digit and value_check_digit(digits) != check:
        raise BarcodeError('value_check_digit')
    value = Decimal(int(digits)).scaleb(-rule.decimals)
    return Parsed(rule=rule, item_code=item_code, value=value,
                  price=value if rule.value_type == PRICE else None,
                  weight_kg=value if rule.value_type == WEIGHT else None)
