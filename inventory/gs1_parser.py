# inventory/gs1_parser.py

"""
GS1 element strings (DataBar Expanded, GS1-128, DataMatrix): application identifiers (AIs) followed by their values,
e.g. GTIN, net weight, price, use-by date and batch. Pure Python, no Django: the till's JavaScript parser (G17)
mirrors it, and both are tested against ``gs1_cases`` in ``barcode_fixtures.json``.

Scanners send FNC1 as the GS character, which ends a variable-length field; the human-readable form ``(01)…(17)…``
is read too. A variable-length field without its separator runs to the end of the string: we never look inside a
field for the next AI, so a field that overruns its maximum length is a ``format`` error (ask for a rescan).
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

GS = '\x1d'
# Only what scanners add around a code; str.strip() would also eat GS (and JavaScript's trim() differs).
_WHITESPACE = ' \t\r\n'
_SYMBOLOGY_IDS = (']e0', ']C1', ']d2', ']Q3')
_BRACKETED = re.compile(r'\(([0-9]{2,4})\)([^()]*)')  # ASCII digits only, as in JavaScript
# GS1 character set 82 (GenSpecs 7.11), allowed in a batch number.
_CSET82 = frozenset('!"%&\'()*+,-./0123456789:;<=>?ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz')
_KG, _PENNY = Decimal('0.001'), Decimal('0.01')

# AI -> (fixed length or None, maximum length, kind). Each kind may appear once.
_AIS = {
    '01': (14, 14, 'gtin'),
    '10': (None, 20, 'batch'),
    '11': (6, 6, 'production_date'),
    '13': (6, 6, 'packaging_date'),
    '15': (6, 6, 'best_before'),
    '17': (6, 6, 'expiry'),
    **{f'310{n}': (6, 6, 'weight_kg') for n in range(6)},
    **{f'392{n}': (None, 15, 'price') for n in range(4)},
}


class GS1Error(ValueError):
    """``reason``: ``format``, ``unknown_ai``, ``check_digit`` (GTIN), ``date`` or ``value`` (fractional pence)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class GS1Parsed:
    gtin: str | None
    batch: str | None
    weight_kg: Decimal | None  # 3 dp, or exact when the label carries more decimals
    price: Decimal | None  # GBP, 2 dp
    expiry: date | None  # AI 17, use by
    best_before: date | None  # AI 15
    expired: bool  # expiry is before today
    elements: dict  # AI -> raw value, as scanned


def gtin_check_digit(digits):
    """GS1 mod-10 check digit for the data digits of a GTIN (weights 3, 1, 3, … from the right)."""
    total = sum(int(digit) * (1 if index % 2 else 3) for index, digit in enumerate(reversed(digits)))
    return (10 - total % 10) % 10


def parse_gs1(code, today):
    """Read a scanned element string; raises ``GS1Error``. ``today`` decides the century and ``expired``."""
    code = code.strip(_WHITESPACE)
    for identifier in _SYMBOLOGY_IDS:
        if code.startswith(identifier):
            code = code[len(identifier):]
            break
    if code.startswith(GS):
        code = code[1:]  # FNC1 in first position
    if not code:
        raise GS1Error('format')
    pairs = _bracketed(code) if code.startswith('(') else _plain(code)
    elements, values = {}, {}
    for ai, raw in pairs:
        fixed, maximum, kind = _AIS[ai]
        if kind in values or not raw or len(raw) > maximum or (fixed and len(raw) != fixed):
            raise GS1Error('format')
        elements[ai], values[kind] = raw, _value(ai, kind, raw, today)
    expiry = values.get('expiry')
    return GS1Parsed(gtin=values.get('gtin'), batch=values.get('batch'), weight_kg=values.get('weight_kg'),
                     price=values.get('price'), expiry=expiry, best_before=values.get('best_before'),
                     expired=expiry is not None and expiry < today, elements=elements)


def _bracketed(code):
    if GS in code:
        raise GS1Error('format')
    pairs, end = [], 0
    for match in _BRACKETED.finditer(code):
        if match.start() != end:
            break
        if match.group(1) not in _AIS:
            raise GS1Error('unknown_ai')
        pairs.append(match.groups())
        end = match.end()
    if end != len(code):
        raise GS1Error('format')
    return pairs


def _plain(code):
    pairs, index = [], 0
    while index < len(code):
        ai = _read_ai(code, index)
        index += len(ai)
        fixed = _AIS[ai][0]
        if fixed:
            end = index + fixed
            if end > len(code):
                raise GS1Error('format')
            following = end + 1 if code[end:end + 1] == GS else end  # a separator after a fixed field is tolerated
        else:
            end = code.find(GS, index)
            end, following = (len(code), len(code)) if end < 0 else (end, end + 1)
        pairs.append((ai, code[index:end]))
        index = following
    return pairs


def _read_ai(code, index):
    head = code[index:index + 2]
    if len(head) < 2 or not (head.isascii() and head.isdigit()):
        raise GS1Error('format')
    for length in (2, 4):
        if code[index:index + length] in _AIS:
            return code[index:index + length]
    raise GS1Error('unknown_ai')


def _value(ai, kind, raw, today):
    if kind == 'batch':
        if not set(raw) <= _CSET82:
            raise GS1Error('format')
        return raw
    if not (raw.isascii() and raw.isdigit()):
        raise GS1Error('format')
    if kind == 'gtin':
        if gtin_check_digit(raw[:13]) != int(raw[13]):
            raise GS1Error('check_digit')
        return raw
    if kind == 'weight_kg':
        weight = Decimal(int(raw)).scaleb(-int(ai[3]))
        return weight.quantize(_KG) if int(ai[3]) < 3 else weight
    if kind == 'price':
        price = Decimal(int(raw)).scaleb(-int(ai[3]))
        if price != price.quantize(_PENNY):
            raise GS1Error('value')
        return price.quantize(_PENNY)
    return _date(raw, today)


def _date(raw, today):
    """``YYMMDD``; day 00 is the month's last day; the century follows GS1 GenSpecs 7.12."""
    year, month, day = int(raw[:2]), int(raw[2:4]), int(raw[4:])
    ahead = year - today.year % 100
    century = today.year // 100 * 100 + (-100 if ahead >= 51 else 100 if ahead <= -50 else 0)
    if not 1 <= month <= 12:
        raise GS1Error('date')
    if day == 0:
        day = calendar.monthrange(century + year, month)[1]
    try:
        return date(century + year, month, day)
    except ValueError:
        raise GS1Error('date') from None
